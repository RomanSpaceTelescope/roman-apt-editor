# Roman APT Population Tools

Generate Roman APT observation files from spreadsheets.

## Tools

All core tools are in the `tools/` folder:

### populate_apt.py
Populate a Roman APT seed XML with CFA (count-rate dependent flat analysis) or tuning observations. Clones the seed's PassPlan once per input table, with one Observation per VISIT_NUMBER group. Supports multi-band input with automatic LED substitution (Band1→Band2/Band3).

```bash
python tools/populate_apt.py --seed seeds/CFA_seed.apt --input data.csv --out output.apt
python tools/populate_apt.py --input data.xlsx --sheet 'Results' --seed seeds/tuning_seed.apt --out tuning.apt
```

### populate_lolo_apt.py
Populate a Roman APT seed XML for LOLO (lamp-on-lamp-off) observations with target and fiducial overrides. Supports grouping multiple rows into multi-LED CRNL observations by (PASSPLAN_LABEL, OBSERVATION_NUMBER).

```bash
python tools/populate_lolo_apt.py --seed seeds/LOLO_seed.apt --input lolo_data.csv --out lolo.apt
```

### generate_darkcal_gopup.py
Generate a Roman WFI OPUP (Observation Plan Upload Package) as a `.tgz` tarball from dark/cal sequences (CFA, flat, dark). Merges rows by VISIT_NUMBER and generates OPS/VST files with proper directory structure. **Note:** Use APT files directly for LOLO observations.

```bash
python tools/generate_darkcal_gopup.py --csv examples/cfa/data.csv --opup_name 2026334010112_2026169141359 \
  --prog 123 --exec 1 --pass_num 1 --seg 1
```

### rcs_apt_helper.py
Helper library for constructing APT strings from TPT review CSVs. Provides utilities for reading review tables, generating LampState strings, and formatting output. Used by populate_apt.py and populate_lolo_apt.py.

### diagnose_csv.py
Diagnostic tool that correlates a TPT review CSV with the MA-table reference YAML to compute timing and count diagnostics. Outputs effective exposure time, read patterns, and total counts per row.

```bash
python tools/diagnose_csv.py --csv data.csv --yaml reference/ma_table_ref_revG.yaml
```

## Quick Start

```bash
# CFA observations (count-rate dependent flat analysis)
python tools/populate_apt.py --seed seeds/CFA_seed.apt --input examples/cfa/data.csv --out output.apt

# LOLO observations (lamp-on-lamp-off calibration)
python tools/populate_lolo_apt.py --seed seeds/LOLO_seed.apt --input examples/lolo/data.csv --out output.apt

# With Excel sheet selection
python tools/populate_apt.py --seed seeds/tuning_seed.apt --input examples/tuning/260615_sRCS_WFI_flight_tuning_CFA_APT.xlsx --out output.apt

```

## Folder Structure

```
tools/              Core APT population and diagnostic scripts
seeds/              Seed APT template files (.apt)
examples/           Example inputs and outputs
  ├── cfa/          CFA example data and outputs
  ├── lolo/         LOLO example data and outputs
  └── tuning/       Tuning example data and outputs
reference/          Documentation and reference data
  ├── ma_table_ref_revG.yaml        Multi-accumulate table reference
  └── sRCS_precharge_explainer.html Precharge calculation explanation
data/               CSV diagnostic and test files
notebooks/          Jupyter notebooks for analysis and exploration
helpers/            Experimental utilities (not maintained)
backups/            APT backup files (.aptbackup)
```

## Input Columns

### Both tools require:
- `VISIT_NUMBER` — observation grouping
- `NEXP` — number of exposures
- `RESULTANTS_PER_EXPOSURE` — resultants per exposure (emitted as `R=value`)
- `MA_TABLE` — multi-accumulate table
- `SRCS_LEDB1`, `SRCS_LEDB2` — LED names or empty (empty = dark)
- `SRCS_LEDB1_FLUX`, `SRCS_LEDB2_FLUX` — LED fluxes
- `SRCS_LEDB1_PRECHARGE_DURATION`, `SRCS_LEDB1_PRECHARGE_FLUX` — precharge (B1)
- `SRCS_LEDB2_PRECHARGE_DURATION`, `SRCS_LEDB2_PRECHARGE_FLUX` — precharge (B2)
- `WFI_SRCS_LEDB1_CLEANUP`, `WFI_SRCS_LEDB2_CLEANUP` — cleanup flags (YES/NO)

### populate_lolo_apt.py only:
- `PASSPLAN_LABEL` — PassPlan grouping (e.g., "F087-NGC-6819-1")
- `OBSERVATION_NUMBER` — sequence within PassPlan (1, 2, 3)
- `OBSERVATION_TYPE` — "dark", "lit", or "crnl"
- `TARGET` — target number or "NONE"
- `OPTICAL_ELEMENT` — filter (e.g., "F087", "F184")
- `FIDUCIAL_APERTURE` — aperture override (e.g., "WFI01_FULL") or empty

## Multi-row CRNL

Multiple rows with same `PASSPLAN_LABEL` + `OBSERVATION_NUMBER` aggregate into one observation:

```
F087-NGC-6819, 3, crnl, ..., LED16, 2, 30, 111.8, NO
F087-NGC-6819, 3, crnl, ..., LED16, 20, 30, 865.4, NO
```

Generates:
```
1, R=13, LED16=2.0 (pre=30,111.8)
2, R=13, LED16=20.0 (pre=30,865.4)
```

## Precharge Calculation

Use the precharge calculator from `../roman-rcs-tools/`:

```bash
precharge-calculator --side B --target-flux 500 --duration 30
```

The output is the precharge flux value to use in the CSV/spreadsheet columns `SRCS_LEDB*_PRECHARGE_FLUX`.

## Examples

See `examples/` folder for input data and outputs:

### CFA (Count-Rate Dependent Flat Analysis)
- `examples/cfa/260615_sRCS_WFI_flight_tuning_CFA_APT.xlsx` — Flight tuning input
- `examples/cfa/CFA_all_bands.apt` — Example output with all bands
- `examples/cfa/CFA_ladder_stacked.png` — CFA ladder visualization

### LOLO (Lamp-On-Lamp-Off Calibration)
- `examples/lolo/lolo_input_example.csv` — Basic LOLO example
- `examples/lolo/lolo_input_MRT12c.csv` — MRT 12c example
- `examples/lolo/lolo_MRT12c.apt` — Example LOLO output
- `examples/lolo/APT_1024_LOLO.csv` — APT 1024 LOLO input

### Tuning
- `examples/tuning/260605_sRCS_WFI_flight_tuning_2hr_APT_upstep.xlsx` — Flight tuning input
- `examples/tuning/tuning.apt` — Example tuning output

## Requirements

- Python ≥ 3.9
- pandas, numpy, openpyxl
