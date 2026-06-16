# Roman APT Population Tools

Generate Roman APT observation files from spreadsheets.

## Quick Start

```bash
# CFA observations (count-rate dependent flat analysis)
python populate_apt.py --seed CFA_seed.apt --input data.csv --out output.apt

# LOLO observations (lamp-on-lamp-off calibration)
python populate_lolo_apt.py --seed LOLO_seed.apt --input data.csv --out output.apt

# With Excel sheet selection
python populate_apt.py --input data.xlsx --sheet "Sheet1" --out output.apt
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

Use the precharge calculator from [roman-rcs-tools](https://github.com/RomanSpaceTelescope/roman-rcs-tools):

```bash
python precharge_calculator.py --led LED16 --target-flux 500.0 --duration 30
```

Output is the precharge flux value to use in the spreadsheet.

## Examples

### CFA (Count-Rate Dependent Flat Analysis)
- `CFA_seed.apt` — Seed file
- `CFA_all_bands.apt` — Example output with all bands configured
- `260615_sRCS_WFI_flight_tuning_CFA_APT.xlsx` — Flight tuning data example

### Tuning
- `tuning_seed.apt` — Seed file
- `260615_sRCS_WFI_flight_tuning_CFA_APT.xlsx` — Flight tuning data example

### LOLO (Lamp-On-Lamp-Off Calibration)
- `LOLO_seed.apt` — Seed file
- `lolo_input_example.csv` — Basic LOLO example
- `lolo_input_MRT12c.csv` — MRT 12c with LED12/F087 and LED16/F184

## Requirements

- Python ≥ 3.9
- pandas, numpy, openpyxl
