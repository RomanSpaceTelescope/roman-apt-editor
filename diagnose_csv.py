"""Diagnostic correlator: TPT review CSV × MA-table YAML → timing/counts CSV.

For each row of a TPT review CSV, looks up the row's ``MA_TABLE`` in the MA-table
reference YAML and pulls ``effective_exposure_time[RESULTANTS_PER_EXPOSURE - 1]``
— that's the integration time of a single exposure with that resultant count.

It then computes:

- ``ReadPattern``          : ``science_read_pattern`` entry for the chosen
                             resultant (frame indices, semicolon-joined)
- ``ExposureTime_s``       : per-exposure effective integration time from YAML
- ``VisitExposureTime_s``  : ExposureTime_s × NEXP  (time the visit spends on
                             this row's pattern)
- ``LEDFluxSum``           : SRCS_LEDB1_FLUX + SRCS_LEDB2_FLUX
                             (NaN treated as 0 — only one channel may be lit)
- ``TotalCounts``          : ExposureTime_s × LEDFluxSum  (counts in **one**
                             exposure — multiply by ``NEXP`` for the row total)

One row per CSV row. Per-visit totals are easy to recompute downstream by
grouping on ``Visit``; the console summary still prints per-CSV totals.

Usage:
    # Band 1 only (default):
    python diagnose_csv.py
    # or pick CSVs explicitly:
    python diagnose_csv.py --csv Band1all_CFA.csv Band1hf_CFA.csv
    # custom YAML / output dir:
    python diagnose_csv.py --yaml ma_table_ref_revG.yaml --outdir diagnostics
"""

import argparse
import glob
import os

import numpy as np
import pandas as pd
import yaml

from rcs_apt_helper import read_review_csv


def load_ma_tables(yaml_path):
    """
    Return ``{ma_table_name: {'effective_exposure_time': [...],
                              'integration_duration': [...],
                              'frame_time': float,
                              'num_science_resultants': int}}``
    keyed by the human-friendly ``ma_table_name`` so lookups match the CSV's
    ``MA_TABLE`` column directly.
    """
    with open(yaml_path) as f:
        doc = yaml.safe_load(f)
    out = {}
    for entry in doc.get('science_tables', {}).values():
        out[entry['ma_table_name']] = {
            'effective_exposure_time': entry['effective_exposure_time'],
            'integration_duration': entry['integration_duration'],
            'science_read_pattern': entry['science_read_pattern'],
            'frame_time': entry['frame_time'],
            'num_science_resultants': entry['num_science_resultants'],
        }
    return out


def lookup_resultant(ma_tables, ma_name, nres):
    """
    Return the timing + read-pattern entries the diagnostic needs for
    ``MA_TABLE=ma_name`` at ``RESULTANTS_PER_EXPOSURE=nres``.

    Raises with a clear message if the table or the resultant index is out of
    range — these are configuration errors that should fail loudly, not
    silently produce NaN.

    Returns:
    tuple[float, list[int]]: (effective_exposure_time_s, science_read_pattern)
    where the read pattern is the list of frame indices read out for that
    resultant (e.g. ``[1]`` for IM_DIAGNOSTIC R=1, ``[12]`` for DROP_10_OF_11
    R=2 — a single-frame resultant; multi-frame patterns appear in tables
    that average reads).
    """
    if ma_name not in ma_tables:
        raise KeyError(f'MA table {ma_name!r} not found in YAML; '
                       f'known tables include {sorted(ma_tables)[:5]}…')
    table = ma_tables[ma_name]
    eet = table['effective_exposure_time']
    nres = int(nres)
    if not 1 <= nres <= len(eet):
        raise ValueError(
            f'MA table {ma_name} has {len(eet)} resultants; row asked for R={nres}.'
        )
    return float(eet[nres - 1]), list(table['science_read_pattern'][nres - 1])


def diagnose(csv_path, ma_tables):
    """
    Build the diagnostic DataFrame for one TPT review CSV — one row per input
    CSV row, with the row's MA-table-derived integration time and the
    LED-flux-driven count estimate attached.
    """
    df = read_review_csv(csv_path)

    rows = []
    for visit_num, visit_rows in df.groupby('VISIT_NUMBER', sort=False):
        for i, (_, row) in enumerate(visit_rows.iterrows(), start=1):
            ma = row['MA_TABLE']
            nres = row['RESULTANTS_PER_EXPOSURE']
            nexp = int(row['NEXP'])

            exptime, read_pattern = lookup_resultant(ma_tables, ma, nres)
            row_exptime = exptime * nexp

            # Treat blank LEDs as 0 flux so a row with only one channel lit
            # still gets the right LEDFluxSum. read_review_csv leaves numeric
            # NaN as float('nan'), which is truthy — coerce explicitly.
            f1 = 0.0 if pd.isna(row['SRCS_LEDB1_FLUX']) else float(row['SRCS_LEDB1_FLUX'])
            f2 = 0.0 if pd.isna(row['SRCS_LEDB2_FLUX']) else float(row['SRCS_LEDB2_FLUX'])
            flux_sum = f1 + f2
            # Per-exposure counts only; NEXP-scaled total left to the consumer.
            row_counts = exptime * flux_sum

            rows.append({
                'Visit': int(visit_num),
                'Row': i,
                'MA_TABLE': ma,
                'Resultants': int(nres),
                'NEXP': nexp,
                # Frame indices read out for this resultant; usually a single
                # frame but DROP_* tables have multi-frame averages further in.
                'ReadPattern': ';'.join(str(x) for x in read_pattern),
                'ExposureTime_s': round(exptime, 4),
                'VisitExposureTime_s': round(row_exptime, 4),
                'LEDB1': row['SRCS_LEDB1'] or '',
                'LEDB1_Flux': float(f1),
                'LEDB2': row['SRCS_LEDB2'] or '',
                'LEDB2_Flux': float(f2),
                'LEDFluxSum': round(flux_sum, 4),
                'TotalCounts': round(row_counts, 2),
            })

    return pd.DataFrame(rows)


def diag_path_for(csv_path, outdir):
    """``Band1all_CFA.csv`` → ``<outdir>/Band1all_CFA_diag.csv``."""
    base = os.path.splitext(os.path.basename(csv_path))[0]
    return os.path.join(outdir, f'{base}_diag.csv')


def main():
    parser = argparse.ArgumentParser(
        description='Correlate TPT review CSVs with MA-table YAML to estimate '
                    'per-row exposure times and total LED counts.',
    )
    parser.add_argument('--yaml', default='ma_table_ref_revG.yaml',
                        help='Path to the MA-table reference YAML (default: ma_table_ref_revG.yaml).')
    parser.add_argument('--csv', nargs='+', default=None,
                        help='CSV files to diagnose. Default: every Band1*_CFA.csv next to the YAML.')
    parser.add_argument('--outdir', default='.',
                        help='Directory for the diagnostic CSVs (default: alongside the input).')
    args = parser.parse_args()

    csv_paths = args.csv if args.csv else sorted(glob.glob('Band1*_CFA.csv'))
    if not csv_paths:
        parser.error('No CSVs supplied and no Band1*_CFA.csv found in CWD.')

    os.makedirs(args.outdir, exist_ok=True)
    ma_tables = load_ma_tables(args.yaml)

    for csv_path in csv_paths:
        diag = diagnose(csv_path, ma_tables)
        out_path = diag_path_for(csv_path, args.outdir)
        diag.to_csv(out_path, index=False)
        n_visits = diag['Visit'].nunique()
        total_time = diag['VisitExposureTime_s'].sum()
        # TotalCounts is per-exposure now; scale by NEXP for the rolled-up total.
        total_counts = (diag['TotalCounts'] * diag['NEXP']).sum()
        print(f'{os.path.basename(csv_path)} → {out_path}: '
              f'{n_visits} visits, {total_time:.1f}s total integration, '
              f'{total_counts:.3e} total counts (sum of LEDFluxSum × time × NEXP).')


if __name__ == '__main__':
    main()
