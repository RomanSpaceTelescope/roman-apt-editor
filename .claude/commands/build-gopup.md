---
description: Generate a Roman WFI CFA OPUP (.tgz) from a CFA CSV spreadsheet
allowed-tools: Bash(python generate_gopup.py *)
---

Generate a Roman WFI OPUP package from a CFA calibration spreadsheet.

## What this does

Reads a CFA CSV where each row is one LED sub-group of exposures. Rows sharing
the same VISIT_NUMBER are merged into a single .vst file. The output is a
fully-structured OPUP .tgz containing an ODF JSON, a file manifest, and an
inner SCF .tgz with one .vst per unique visit and an .ops file.

## Steps

1. Identify the input CSV. If not given in args, look for a `*_CFA.csv` in the
   current directory.

2. Collect required arguments. Ask the user for any that are missing:
   - `--opup_name` — timestamp string, e.g. `2026334010112_2026169141359`
   - `--prog` — program number (integer), e.g. `700`

   Optional (have defaults):
   - `--exec` (default 1), `--pass_num` (default 1), `--seg` (default 1)
   - `--early` / `--late` / `--cutoff` — scheduling windows
     (defaults: 2025-001-00:00:00 / 2031-001-01:00:00 / 2032-001-01:00:00)
   - `--purpose` — intended purpose string for ODF
   - `--ma_yaml` — path to ma_table_ref_revG.yaml
     (default: ../roman-apt-editor/ma_table_ref_revG.yaml)
   - `--odir` — output directory (default: same directory as CSV)

3. Run:
   ```
   python generate_gopup.py --csv <csv> --opup_name <name> --prog <prog> [...]
   ```

4. Report the output path and summary (visits generated, SCF name).

## CSV format (CFA-style)

Required columns:
- `VISIT_NUMBER` — groups rows into one .vst file
- `NEXP` — number of exposures in this sub-group
- `MA_TABLE` — one of: IM_DIAGNOSTIC, DROP_1_OF_2, DROP_2_OF_3, DROP_3_OF_4, DROP_4_OF_5, DROP_10_OF_11
- `RESULTANTS_PER_EXPOSURE` — includes the implicit pedestal; READFRMS is looked up from the MA table YAML
- `SRCS_LEDB1` / `SRCS_LEDB1_FLUX` / `SRCS_LEDB1_SETTLE` / `SRCS_LEDB1_PRECHARGE_DURATION` / `SRCS_LEDB1_PRECHARGE_FLUX`
- `SRCS_LEDB2` / `SRCS_LEDB2_FLUX` / `SRCS_LEDB2_SETTLE` / `SRCS_LEDB2_PRECHARGE_DURATION` / `SRCS_LEDB2_PRECHARGE_FLUX`
- `WFI_SRCS_LEDB1_CLEANUP` / `WFI_SRCS_LEDB2_CLEANUP` — YES emits WFI_SRCS_LED_ENA_DIS_F interleaved in GROUP 03 immediately after that sub-group's last exposure
- `ACT_TIME` — exposure time in seconds (used in ODF metadata)

## Key encoding rules

- **SETTLE=YES** → emit `WFI_SRCS_LED_SET_CURRENT_F` with SETTLE+PRECHARGE args
- **SETTLE=NO** → LED already running from previous sub-group; skip the command
- **READFRMS** per exposure = `science_read_pattern[RPE-2][-1]` from the YAML
  (RPE-1 science resultants because the pedestal is implicit)
- **READFRMS reload** in GROUP 03 whenever RPE changes between sub-groups
- **WFI_EXPOSURE_START_F** fires once (first exposure); all subsequent
  exposures use `WFI_SET_USER_ID_SCI_EXPOSE_F` with a leading `; ` line
- **CLEANUP=YES** → emit `WFI_SRCS_LED_ENA_DIS_F` immediately after that sub-group's last exposure, interleaved in GROUP 03 (no GROUP 04)

## Output structure

```
{opup_name}_opup.tgz
  {part1}_{part2}_odf.json
  {part1}_{part2}_fm.man
  SCF_{part1}.tgz
    OPS_{part1}.ops
    V{visit_id}.vst   (one per unique VISIT_NUMBER)
```
