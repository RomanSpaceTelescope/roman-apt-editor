#!/usr/bin/env python
"""
generate_gopup.py

Generate a Roman WFI OPUP (Observation Plan Upload Package) from a CFA or
LOLO-style CSV spreadsheet.

Rows sharing the same VISIT_NUMBER are merged into a single .vst file with
LED changes interleaved inside GROUP 03.

Output structure:
  {opup_name}_opup.tgz
    ├── {part1}_{part2}_odf.json
    ├── {part1}_{part2}_fm.man
    └── SCF_{part1}.tgz
          ├── OPS_{part1}.ops
          └── V{visit_id}.vst   (one per unique VISIT_NUMBER)

Usage:
  python generate_gopup.py --csv Band5hf_CFA.csv \\
      --opup_name 2026334010112_2026169141359 \\
      --prog 123 --exec 1 --pass_num 1 --seg 1 \\
      [--ma_yaml path/to/ma_table_ref_revG.yaml] \\
      [--early "2025-001-00:00:00"] [--late "2031-001-01:00:00"] \\
      [--cutoff "2032-001-01:00:00"] [--odir ./output/]
"""

import argparse
import csv
import io
import json
import tarfile
import yaml
from collections import OrderedDict
from pathlib import Path

# ─── Default YAML path ───────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
DEFAULT_MA_YAML = _HERE / 'ma_table_ref_revG.yaml'

# ─── MA table lookup ─────────────────────────────────────────────────────────

def load_ma_tables(yaml_path):
    """
    Return dict: ma_table_name -> {fsw_slot, read_pattern}
    read_pattern[i] = last frame number of the (i+1)th science resultant.
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    tables = {}
    for entry in data['science_tables'].values():
        name = entry['ma_table_name']
        slot = entry['fsw_slot_number']
        pat  = [r[-1] for r in entry['science_read_pattern']]
        tables[name] = {'fsw_slot': slot, 'read_pattern': pat}
    return tables


def readfrms_for_rpe(ma_tables, ma_table, rpe):
    """
    READFRMS per exposure = last frame of the (RPE-1)th science resultant.
    The pedestal is the implicit first resultant, so science resultants = RPE-1.
    """
    pat = ma_tables[ma_table]['read_pattern']
    return pat[rpe - 2]   # 0-indexed: RPE-1 science resultants → index RPE-2


def fsw_slot(ma_tables, ma_table):
    return ma_tables[ma_table]['fsw_slot']


# ─── ID helpers ─────────────────────────────────────────────────────────────

def build_visit_id_digits(prog, xp, pass_num, seg, obs):
    """Return the 19-digit visit ID string (no leading 'V', vis always 001)."""
    return f"{prog:05d}{xp:02d}{pass_num:03d}{seg:03d}{obs:03d}001"


def build_sci_id(vid_digits, act_num, exp_index):
    """
    Build 32-char SCI_ID.
    Format: visit_id(19) + '031' + act_num(2) + exp_index(4) + '0000'
    act_num  – GROUP 03 act number where this exposure fires
    exp_index – 1-based global exposure counter within the visit
    """
    return f"{vid_digits}031{act_num:02d}{exp_index:04d}0000"


# ─── LED command helper ──────────────────────────────────────────────────────

def led_cmd(led, bank, flux, settle, pre_dur, pre_flux):
    """
    Always 7-arg form:
      WFI_SRCS_LED_SET_CURRENT_F(led, bank, flux, settle, precharge, dur, pf)
    comment: BAND,BANK,FLUX,SETTLE,PRECHARGE,PRECHARGE_DURATION,PRECHARGE_FLUX
    """
    s = settle.upper() if settle else 'SETTLE'
    if pre_dur and float(pre_dur) > 0:
        pc, dur, pf = 'PRECHARGE', float(pre_dur), float(pre_flux)
    else:
        pc, dur, pf = 'NOPRECHARGE', 1, 0.0
    cmd = (f'WFI_SRCS_LED_SET_CURRENT_F("{led}",{bank},{float(flux):.3f},'
           f'"{s}","{pc}",{dur:.0f},{pf:.3f})')
    cmt = 'BAND,BANK,FLUX,SETTLE,PRECHARGE,PRECHARGE_DURATION,PRECHARGE_FLUX'
    return cmd, cmt


def row_led_cmds(row):
    """
    Return list of (cmd, cmt) for each active LED bank in a CSV row.
    LEDs with SETTLE=NO are already running from the previous sub-group
    and do not need to be re-commanded.
    """
    cmds = []
    for col, bank in [('SRCS_LEDB1', 1), ('SRCS_LEDB2', 2)]:
        led = row.get(col, '').strip()
        if not led:
            continue
        settle_raw = row.get(f'{col}_SETTLE', '').strip().upper()
        if settle_raw == 'NO':
            continue          # LED already on — no command needed
        flux     = row.get(f'{col}_FLUX', '').strip()
        settle   = 'SETTLE'   # only YES reaches here
        pre_dur  = row.get(f'{col}_PRECHARGE_DURATION', '').strip()
        pre_flux = row.get(f'{col}_PRECHARGE_FLUX', '').strip()
        cmds.append(led_cmd(led, bank, flux, settle, pre_dur, pre_flux))
    return cmds


def row_cleanup_cmds(row):
    """Return list of (cmd, cmt) for LED cleanup commands from a CSV row."""
    cmds = []
    for suffix, bank in [('LEDB1', 1), ('LEDB2', 2)]:
        if row.get(f'WFI_SRCS_{suffix}_CLEANUP', '').strip().upper() == 'YES':
            cmds.append((f'WFI_SRCS_LED_ENA_DIS_F({bank},"DISABLE","DUMMY")',
                         'BANK,ACTION,LED'))
    return cmds


def is_dark_row(row):
    return (not row.get('SRCS_LEDB1', '').strip() and
            not row.get('SRCS_LEDB2', '').strip())


# ─── Visit file generator ────────────────────────────────────────────────────

def generate_vst(group, ma_tables, prog, xp, pass_num, seg, obs_num,
                 early, late, cutoff):
    """
    Generate .vst text for a visit group (one or more CSV rows sharing
    the same VISIT_NUMBER).

    GROUP 03 pattern (LOLO-style):
      - LED setup for first LED sub-group
      - WFI_EXPOSURE_START_F  (fires once; exp 1 SCI_ID pre-loaded in SEQ 4)
      - ';' + WFI_SET_USER_ID_SCI_EXPOSE_F for each subsequent exposure
      - When LED changes: insert LED_SET_CURRENT mid-group
      - When RPE changes: also insert WFI_LOAD_SCI_MA_SETREADFRMS_F

    Cleanup commands go in GROUP 04.
    """
    vid        = build_visit_id_digits(prog, xp, pass_num, seg, obs_num)
    visit_id   = 'V' + vid
    gw_user_id = vid + '1'

    first_row  = group[0]
    ma_table   = first_row['MA_TABLE'].strip()
    first_rpe  = int(first_row['RESULTANTS_PER_EXPOSURE'])
    first_rf   = readfrms_for_rpe(ma_tables, ma_table, first_rpe)
    slot       = fsw_slot(ma_tables, ma_table)
    ma_str     = f'{slot:03d}_{ma_table}'

    dark       = all(is_dark_row(r) for r in group)
    hdr_comment = 'WFI Dark Cal No Slew' if dark else 'WFI sRCS-FLAT Cal No Slew'

    # Pre-compute GROUP 03 acts to know where EXPOSURE_START_F lands (for exp 1 SCI_ID)
    first_led_cmds = row_led_cmds(first_row)
    expose_start_act = len(first_led_cmds) + 1   # act number of EXPOSURE_START_F

    L = []

    # ── Header + VISIT line ───────────────────────────────────────────────────
    L.append(f';@ {hdr_comment}')
    L.append(f'VISIT, {visit_id}, EARLY={early}, LATE={late}, CUTOFF={cutoff}, CONVST=WFI_OPS;')

    # ── GROUP 01 ──────────────────────────────────────────────────────────────
    L.append('GROUP, 01, CONGRP=NONE;')

    L.append(' SEQ, 1, CONSEQ=NONE;')
    L.append('  ACT, 01, SCF_AM_MODE_F("ENG"); ACS_MODE')

    L.append(' SEQ, 2, CONSEQ=WFI_OPS;')
    L.append('  ACT, 01, WFI_MCE_EWA_MOVE_ABS_F("HOME_DARK"); FILTER')

    L.append(' SEQ, 3, CONSEQ=WFI_OPS;')
    L.append('  ACT, 01, WFIF_FGS_MODE_CHG_F("STANDBY","WIM_DARK_CAL"); MODE,CNFG')
    L.append(f'  ACT, 02, WFI_FPE_SET_USER_ID_GW_F(0,"{gw_user_id}"); SCUNUM,GWID')
    for sce in range(1, 19):
        L.append(f'  ACT, {sce + 2:02d}, WFI_FPE_SCE_GW_CONFIG_LOC_F({sce},16,16); SCENUM,X_START,Y_START')

    # SEQ 4: MA load + exp 1 SCI_ID only
    exp1_sci = build_sci_id(vid, expose_start_act, 1)
    L.append(' SEQ, 4, CONSEQ=WFI_OPS;')
    L.append(f'  ACT, 01, WFI_LOAD_SCI_MA_SETREADFRMS_F(0,"{ma_str}",{first_rf}); WFI_DET,WFI_SCI_TABLE,READFRAMES')
    L.append(f'  ACT, 02, WFI_FPE_SET_USER_ID_SCI_F(0,"{exp1_sci}"); SCUNUM,SCI_ID')

    # ── GROUP 02 ──────────────────────────────────────────────────────────────
    L.append('GROUP, 02, CONGRP=WFI_OPS;')
    L.append(' SEQ, 1, CONSEQ=WFI_OPS;')
    L.append('  ACT, 01, OBS_ENG_CHECK_WFI_F;')

    # ── GROUP 03: interleaved LED + expose ───────────────────────────────────
    L.append('GROUP, 03, CONGRP=WFI_OPS;')
    L.append(' SEQ, 1, CONSEQ=WFI_OPS;')

    act         = 1      # running act counter in GROUP 03
    exp_global  = 1      # running exposure counter across all LED groups
    prev_rpe    = first_rpe
    fired_start = False

    for row in group:
        nexp = int(row['NEXP'])
        rpe  = int(row['RESULTANTS_PER_EXPOSURE'])
        rf   = readfrms_for_rpe(ma_tables, ma_table, rpe)

        # Emit LED setup commands for this sub-group (SETTLE=NO rows skipped)
        for cmd, cmt in row_led_cmds(row):
            L.append(f'  ACT, {act:02d}, {cmd}; {cmt}')
            act += 1

        # If RPE changed since the last sub-group, reload MA table
        if rpe != prev_rpe:
            L.append(f'  ACT, {act:02d}, WFI_LOAD_SCI_MA_SETREADFRMS_F(0,"{ma_str}",{rf}); WFI_DET,WFI_SCI_TABLE,READFRAMES')
            act += 1
            prev_rpe = rpe

        # Emit exposures
        for _ in range(nexp):
            if not fired_start:
                L.append(f'  ACT, {act:02d}, WFI_EXPOSURE_START_F;')
                fired_start = True
            else:
                sci_id = build_sci_id(vid, act, exp_global)
                L.append('; ')
                L.append(f'  ACT, {act:02d}, WFI_SET_USER_ID_SCI_EXPOSE_F(0,"{sci_id}"); SCUNUM,SCI_ID')
            act += 1
            exp_global += 1

        # Interleave LED disable immediately after this sub-group's exposures
        for cmd, cmt in row_cleanup_cmds(row):
            L.append(f'  ACT, {act:02d}, {cmd}; {cmt}')
            act += 1

    return '\n'.join(L) + '\n'


# ─── OPS / ODF / manifest generators ────────────────────────────────────────

def generate_ops(visit_ids, early, late, cutoff):
    return '\n'.join(f'{v} {early} {late} {cutoff}' for v in visit_ids) + '\n'


def generate_odf(groups, visit_ids, prog, xp, pass_num, seg,
                 early, late, cutoff, intended_purpose):
    visits_json = []
    for rows, vid in zip(groups, visit_ids):
        first = rows[0]
        dark  = all(is_dark_row(r) for r in rows)
        mode  = 'WFI_DARK' if dark else 'WFI_FLAT'
        obs   = vid[9:12]
        vis   = vid[12:15]
        visits_json.append({
            'start':                   f'{early} TAI',
            'duration':                first.get('ACT_TIME', '0').strip(),
            'Visit_ID':                vid,
            'Program_Number':          f'{prog:05d}',
            'Exec_Plan_Number':        f'{xp:02d}',
            'Pass_Number':             f'{pass_num:03d}',
            'Segment_Number':          f'{seg:03d}',
            'Observation_Number':      obs,
            'Visit_Number':            vis,
            'Visit_File_Name':         f'V{vid}.vst',
            'Earliest_Start_Time':     f'{early} TAI',
            'Latest_Start_Time':       f'{late} TAI',
            'Latest_End_Time':         f'{cutoff} TAI',
            'Number_GSACQs':           '1',
            'Guide_Window_ID':         vid + '1',
            'Science_Data_Volume':     '0',
            'Science_Instrument':      'WFI',
            'Science_Instrument_Mode': mode,
            'FGS_Guidance':            'false',
            'WFI_Optical_Element':     'DARK',
            'Realtime_Visit':          'false',
            'Intended_Purpose':        intended_purpose,
            'RA':                      '0.0',
            'DEC':                     '0.0',
            'Position_Angle':          '0.0',
        })
    return {'visits': visits_json}


def generate_manifest(ops_filename, visit_ids, early):
    lines = [f'# SCF File Manifest for OPUP execution earliest start time {early} TAI',
             ops_filename]
    lines += [f'V{v}.vst' for v in visit_ids]
    return '\n'.join(lines) + '\n'


# ─── Archive helpers ─────────────────────────────────────────────────────────

def add_string_to_tar(tf, name, content):
    data = content.encode('utf-8')
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def build_scf_tgz(ops_name, ops_text, visit_map):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        add_string_to_tar(tf, ops_name, ops_text)
        for fname, content in visit_map.items():
            add_string_to_tar(tf, fname, content)
    return buf.getvalue()


def build_opup_tgz(odf_name, odf_json, man_name, man_text, scf_name, scf_bytes):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        add_string_to_tar(tf, odf_name, json.dumps(odf_json, indent=2))
        add_string_to_tar(tf, man_name, man_text)
        info = tarfile.TarInfo(name=scf_name)
        info.size = len(scf_bytes)
        tf.addfile(info, io.BytesIO(scf_bytes))
    return buf.getvalue()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Generate a Roman WFI OPUP from a CFA/LOLO CSV spreadsheet.')
    parser.add_argument('--csv',       required=True)
    parser.add_argument('--opup_name', required=True,
                        help='e.g. 2026334010112_2026169141359')
    parser.add_argument('--prog',      type=int, required=True)
    parser.add_argument('--exec',      type=int, default=1)
    parser.add_argument('--pass_num',  type=int, default=1)
    parser.add_argument('--seg',       type=int, default=1)
    parser.add_argument('--early',   default='2025-001-00:00:00')
    parser.add_argument('--late',    default='2031-001-01:00:00')
    parser.add_argument('--cutoff',  default='2032-001-01:00:00')
    parser.add_argument('--purpose', default='WFI CFA Calibration')
    parser.add_argument('--ma_yaml', default=str(DEFAULT_MA_YAML),
                        help='Path to ma_table_ref_revG.yaml')
    parser.add_argument('--odir',    default=None)
    args = parser.parse_args()

    ma_tables = load_ma_tables(args.ma_yaml)

    # Derive naming
    parts  = args.opup_name.split('_')
    part1  = parts[0]
    part2  = parts[1] if len(parts) > 1 else parts[0]
    opup_file = f'{args.opup_name}_opup.tgz'
    odf_name  = f'{part1}_{part2}_odf.json'
    man_name  = f'{part1}_{part2}_fm.man'
    scf_name  = f'SCF_{part1}.tgz'
    ops_name  = f'OPS_{part1}.ops'

    odir = Path(args.odir) if args.odir else Path(args.csv).parent
    odir.mkdir(parents=True, exist_ok=True)

    # Read CSV
    with open(args.csv, newline='') as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get('NEXP', '').strip()]

    # Group rows by VISIT_NUMBER (preserve order of first appearance).
    groups_ordered = OrderedDict()
    for row in rows:
        key = row['VISIT_NUMBER'].strip()
        groups_ordered.setdefault(key, []).append(row)
    visit_groups = list(groups_ordered.values())

    print(f'CSV rows: {len(rows)}, unique visits: {len(visit_groups)}')

    xp = getattr(args, 'exec')
    visit_ids = []
    visit_map = {}

    for obs_num, group in enumerate(visit_groups, 1):
        vid   = build_visit_id_digits(args.prog, xp, args.pass_num, args.seg, obs_num)
        fname = f'V{vid}.vst'
        content = generate_vst(group, ma_tables, args.prog, xp, args.pass_num,
                               args.seg, obs_num, args.early, args.late, args.cutoff)
        visit_ids.append(vid)
        visit_map[fname] = content

    ops_text  = generate_ops(visit_ids, args.early, args.late, args.cutoff)
    odf_json  = generate_odf(visit_groups, visit_ids, args.prog, xp,
                             args.pass_num, args.seg, args.early, args.late,
                             args.cutoff, args.purpose)
    man_text  = generate_manifest(ops_name, visit_ids, args.early)
    scf_bytes = build_scf_tgz(ops_name, ops_text, visit_map)
    opup_bytes = build_opup_tgz(odf_name, odf_json, man_name, man_text,
                                scf_name, scf_bytes)

    out_path = odir / opup_file
    out_path.write_bytes(opup_bytes)
    print(f'Written: {out_path}')
    print(f'  Visits: {len(visit_ids)}, SCF: {scf_name}')


if __name__ == '__main__':
    main()
