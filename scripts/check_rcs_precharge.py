#!/usr/bin/env python3
"""
check_rcs_precharge.py — audit/correct sRCS LED precharge in an APT flux sequence.

Self-contained. Reads the calibration workbook + an APT CSV, recomputes the
correct credited precharge for every IR (band 4-6) LED row, diffs against the
file, classifies likely issues, and (optionally) writes a corrected CSV.

Encodes the validated model:
  * precharge charge  Q = pc_band * dI,  dI = I_target - I_prev   (mA*s)
  * delivered as       Q = I_pc * t_pc
  * default t_pc = 30 s; if I_pc would exceed the LED max current, pin current
    at the max and EXTEND time (t = Q / I_max); hard cap t <= 300 s.
  * step-aware CREDIT: within a sweep the LED stays lit, so each up-step credits
    the previous equilibrium (I_prev). First step of a sweep is from dark.
  * 10-MINUTE DECAY: once an LED is off it decays to cold; after >~10 min off it
    is fully discharged, so its next use starts from dark again.  CLEANUP=YES is
    the heuristic flag marking that boundary.

Usage:
  python check_rcs_precharge.py SEQUENCE.csv --cal CALIB.xlsx [--side B]
                                [--decay-min 10] [--fix OUT.csv] [--all]
Calibration workbook must have sheets 'H4RG Flux Calibration' and 'LED Calibration'.
"""
import argparse, csv, math, sys
import numpy as np
import pandas as pd

PRECHARGE_BANDS = {4, 5, 6}
# Validated Side-specific precharge constants [mA*s / mA] (NOT the stale
# 2024-03-19 workbook Precharge sheet {1665,1000,800}).
PC = {'A': {4: 2164, 5: 1633, 6: 1040}, 'B': {4: 2164, 5: 1300, 6: 1040}}
T0_DEFAULT = 30.0
DUR_CAP = 300.0
MAX_CODE = 2 * 65535 - 1
LMAX = '<= low max flux\n[e-/pix/s]'
HMIN = '> high min flux\n[e-/pix/s]'
HMAX = 'high max flux\n[e-/pix/s]'


class Cal:
    """Bank-AWARE flux<->code<->current calibration (box 1 = Side A, box 2 = Side B)."""
    def __init__(self, path, side):
        self.side = side.upper()
        box = 1 if self.side == 'A' else 2
        fx = pd.read_excel(path, 'H4RG Flux Calibration')
        fx = fx[fx.box == box]
        # KEY BY (band, bank) — keying by band alone silently uses bank-2 for all LEDs.
        self.ft = {(int(r.band), int(r.bank)): r for _, r in fx.iterrows()}
        ld = pd.read_excel(path, 'LED Calibration')
        ld = ld[ld.box == box]
        self.cv = {int(r.bank): r for _, r in ld.iterrows()}

    def f2code(self, b, k, fl):
        r = self.ft[(b, k)]
        if fl <= r[LMAX]:
            return r['L^3']*fl**3 + r['L^2']*fl**2 + r['L^1']*fl + r['L^0']
        if r[HMIN] < fl < r[HMAX]:
            return r['H^3']*fl**3 + r['H^2']*fl**2 + r['H^1']*fl + r['H^0']
        return None  # dead zone or out of range

    def in_deadzone(self, b, k, fl):
        r = self.ft[(b, k)]
        return r[LMAX] < fl <= r[HMIN]

    def code2flux(self, b, k, code):
        r = self.ft[(b, k)]
        coeffs = ([r['L^3'], r['L^2'], r['L^1'], r['L^0'] - code] if code <= 65535
                  else [r['H^3'], r['H^2'], r['H^1'], r['H^0'] - code])
        roots = np.roots(coeffs)
        real = roots[np.abs(roots.imag) < 1e-6].real        # tolerance, not exact np.isreal
        hi = r[LMAX]*1.2 if code <= 65535 else r[HMAX]*1.2
        cand = real[(real > 0) & (real <= hi)]
        return float(cand.min()) if len(cand) else float(real.max())

    def code2cur(self, k, code):                              # -> mA
        c = self.cv[k]; code = int(code)
        amps = ((code - c['high b'])/c['high m'] if code > 65536
                else (code - c['low b'])/c['low m'])
        return amps * 1e3

    def cur2code(self, k, mA):
        c = self.cv[k]; amps = mA/1e3
        thr = (65535 - c['low b'])/c['low m']                 # current at code 65535
        return round(amps*c['high m'] + c['high b'], 0) if amps > thr \
            else round(amps*c['low m'] + c['low b'], 0)

    def f2i(self, b, k, fl):                                  # flux -> current (mA); None if uncommandable
        code = self.f2code(b, k, fl)
        return None if code is None else self.code2cur(k, code)

    def imax(self, b, k):
        return self.f2i(b, k, self.ft[(b, k)][HMAX] - 2)

    def precharge(self, b, k, target, prev, pc, t0=T0_DEFAULT):
        """Correct credited precharge for one step."""
        it = self.f2i(b, k, target)
        ip = self.f2i(b, k, prev) if prev else 0.0
        if it is None:
            return dict(error='target flux uncommandable (dead zone / out of range)')
        Q = pc[b] * (it - (ip or 0.0))                        # mA*s
        ipc = Q / t0
        dur, clamped = t0, False
        if self.cur2code(k, ipc) > MAX_CODE:                  # would exceed LED max current
            clamped = True
            dur = math.ceil(Q / self.imax(b, k))              # pin at max -> extend time (whole s)
            ipc = Q / dur                                     # recompute current so I*dur == Q exactly
        pflux = self.code2flux(b, k, self.cur2code(k, ipc))
        return dict(pflux=round(pflux, 1), dur=round(dur, 1),
                    current_mA=round(ipc, 2), clamped=clamped, over_cap=dur > DUR_CAP,
                    Q=Q, it=it, ip=(ip or 0.0))


def parse_led(s):
    s = s.strip()
    if not s or len(s) < 5:
        return None
    return int(s[3]), int(s[4])                               # (bank, band) from LED{bank}{band}


def review(seq_path, cal_path, side='B', decay_min=10.0, fix_path=None, show_all=False):
    cal = Cal(cal_path, side)
    pc = PC[side.upper()]
    rows = list(csv.DictReader(open(seq_path)))
    fields = list(rows[0].keys())
    has_cum = 'CUMULATIVE_TIME_SEC' in fields
    slots = [s for s in ('B1', 'B2') if f'SRCS_LED{s}' in fields]

    findings, n_ir, n_ok = [], 0, 0
    last_off = {}                                             # (band,bank) -> cumulative time at last cleanup
    gk = {s: None for s in slots}
    prev = {s: None for s in slots}

    for ri, r in enumerate(rows):
        cum = float(r['CUMULATIVE_TIME_SEC']) if has_cum and r.get('CUMULATIVE_TIME_SEC') else None
        for s in slots:
            led = r.get(f'SRCS_LED{s}', '').strip()
            pl = parse_led(led)
            if r.get('OBSERVATION_TYPE', 'lolo') not in ('lolo', '') or not pl:
                continue
            bank, band = pl
            flux = float(r[f'SRCS_LED{s}_FLUX'])
            g = (r.get('PASSPLAN_LABEL', ''), r.get('OBSERVATION_NUMBER', ''), led)
            if g != gk[s]:
                gk[s] = g
                prev[s] = None                                # new sweep -> from dark (decay assumed complete)

            decay_note = ''
            if pl in last_off and cum is not None and prev[s] is None:
                gap_min = (cum - last_off[pl]) / 60.0
                if 0 <= gap_min < decay_min:
                    decay_note = (f'LED reused {gap_min:.1f} min after last off (<{decay_min:.0f} min '
                                  f'decay) — may still hold charge; could credit further')

            file_dur = r.get(f'SRCS_LED{s}_PRECHARGE_DURATION', '').strip()
            file_pf = r.get(f'SRCS_LED{s}_PRECHARGE_FLUX', '').strip()

            if band in PRECHARGE_BANDS:
                n_ir += 1
                exp = cal.precharge(band, bank, flux, (prev[s] or 0.0), pc)
                issues = []
                if cal.in_deadzone(band, bank, flux):
                    issues.append('TARGET FLUX IN DEAD ZONE (uncommandable for this bank)')
                if 'error' in exp:
                    issues.append(exp['error'])
                elif not file_pf:
                    issues.append('MISSING precharge on an IR band')
                else:
                    fpf, fdur = float(file_pf), float(file_dur or 0)
                    if fdur > DUR_CAP:
                        issues.append(f'DURATION {fdur:.0f}s EXCEEDS {DUR_CAP:.0f}s CAP')
                    fcur = cal.f2i(band, bank, fpf)
                    if fcur is not None:
                        Q_file = fcur * fdur
                        dev = (Q_file - exp['Q']) / exp['Q'] if exp['Q'] else 0
                        if dev < -0.03:
                            ip_eff = exp['it'] - Q_file/pc[band]
                            issues.append(f'UNDER-charged {dev*100:+.0f}% (over-credited: assumes '
                                          f'prev I={ip_eff:.3f} vs true {exp["ip"]:.3f} mA)')
                        elif dev > 0.03:
                            issues.append(f'OVER-charged {dev*100:+.0f}% (under-credited / no credit)')
                        imax = cal.imax(band, bank)
                        if fdur > T0_DEFAULT + 0.5 and fcur < 0.95*imax:
                            issues.append(f'WRONG KNOB: extended time at sub-max current '
                                          f'({fcur:.0f} mA vs max {imax:.0f}); pin at max & shorten')
                        other = 2 if bank == 1 else 1
                        try:
                            ob = cal.precharge(band, other, flux, 0.0, pc)
                            if ('error' not in ob and abs(fpf-ob['pflux']) < abs(fpf-exp['pflux'])
                                    and abs(fpf-ob['pflux']) < 0.01*max(fpf, 1)
                                    and abs(fpf-exp['pflux']) > 0.02*fpf):
                                issues.append(f'POSSIBLE BANK MIX-UP: matches bank-{other} from-dark, not bank-{bank}')
                        except Exception:
                            pass

                ok = not issues
                n_ok += ok
                if not ok or show_all:
                    findings.append(dict(row=ri+2, passplan=r.get('PASSPLAN_LABEL', ''), led=led,
                                         flux=flux, file=f"{file_dur or '-'}s/{file_pf or '-'}",
                                         correct=(f"{exp.get('dur','?')}s/{exp.get('pflux','?')}"
                                                  + (' [clamp@max]' if exp.get('clamped') else '')),
                                         issues='; '.join(issues) or 'OK', decay=decay_note))
                if fix_path and 'error' not in exp:
                    r[f'SRCS_LED{s}_PRECHARGE_FLUX'] = f"{exp['pflux']}"
                    r[f'SRCS_LED{s}_PRECHARGE_DURATION'] = f"{exp['dur']}"
                prev[s] = flux
            else:
                if file_pf:
                    findings.append(dict(row=ri+2, passplan=r.get('PASSPLAN_LABEL', ''), led=led,
                                         flux=flux, file=file_pf, correct='(blank)',
                                         issues='precharge set on a VISIBLE band (1-3) — should be blank',
                                         decay=''))
                prev[s] = flux

            if r.get(f'WFI_SRCS_LED{s}_CLEANUP', '').strip().upper() == 'YES' and cum is not None:
                last_off[(band, bank)] = cum

    print(f'RCS precharge review: {seq_path}')
    print(f'  calibration: {cal_path}  side {side.upper()}  pc={pc}')
    print(f'  IR precharge rows: {n_ir}   passing: {n_ok}   flagged: {n_ir - n_ok}\n')
    if not findings:
        print('  No issues found. OK')
    for f in findings:
        print(f"  row {f['row']:>3} {f['passplan']:<10} {f['led']:<6} flux={f['flux']:<7g}"
              f" file={f['file']:<15} correct={f['correct']:<24} | {f['issues']}")
        if f['decay']:
            print(f"        note: {f['decay']}")
    if fix_path:
        with open(fix_path, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(rows)
        print(f'\n  Wrote corrected sequence -> {fix_path}')
    return findings


def main():
    ap = argparse.ArgumentParser(description='Audit/correct sRCS precharge in an APT sequence CSV.')
    ap.add_argument('sequence', help='APT sequence CSV')
    ap.add_argument('--cal', required=True, help='calibration .xlsx (H4RG Flux Calibration + LED Calibration sheets)')
    ap.add_argument('--side', default='B', choices=['A', 'B'])
    ap.add_argument('--decay-min', type=float, default=10.0, help='LED off-time (min) assumed for full discharge')
    ap.add_argument('--fix', default=None, help='write corrected sequence to this path')
    ap.add_argument('--all', action='store_true', help='show every IR row, not only flagged ones')
    a = ap.parse_args()
    findings = review(a.sequence, a.cal, a.side, a.decay_min, a.fix, a.all)
    sys.exit(1 if any(f['issues'] != 'OK' for f in findings) else 0)


if __name__ == '__main__':
    main()
