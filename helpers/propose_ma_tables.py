"""Propose DROP-based MA tables to reduce data volume for an LED tuning sequence.

Reads a TPT review xlsx (the same schema ``populate_apt.py`` consumes) and,
for every row that uses ``IM_DIAGNOSTIC``, picks a ``DROP_*`` MA table whose
integration time is closest to the original at a smaller number of stored
resultants:

- ``R_old ≥ 15`` → ``DROP_2_OF_3``, R_new = round((R_old+2)/3), floor 5 — same
  integration time over ~3× fewer stored frames per exposure.
- ``9 ≤ R_old ≤ 14`` → ``DROP_1_OF_2``, R_new = round((R_old+1)/2), floor 5 —
  ~2× fewer.
- ``R_old ≤ 8`` → kept (R=3 saturation frames or short ramps; dropping would
  push samples below the 4–5 minimum needed for a robust up-the-ramp fit).

**MA tables cannot change within a single visit.** When the proposed table for
some rows of a visit differs from others (typical pattern: low-flux ramp →
DROP_2_OF_3, mid ramp → DROP_1_OF_2, saturation → IM_DIAGNOSTIC), the visit is
split into contiguous sub-visits, each with a uniform MA table. Visits are
renumbered consecutively in the output. The ``WFI_SRCS_LEDB*_CLEANUP`` column
is preserved as-is — only the original visit's last row keeps ``YES``, so the
LED stays on across split-visit boundaries within the same original visit.

Writes a new xlsx with:

- ``in``         — drop-in replacement for the original ``in`` sheet, with
                   updated ``MA_TABLE`` / ``RESULTANTS_PER_EXPOSURE`` /
                   ``ACT_TIME`` / ``VISIT_NUMBER`` columns. Feed this to
                   ``populate_apt.py``.
- ``comparison`` — per-row before/after with integration time and
                   stored-frame deltas, plus the new visit assignment.
- ``README``     — methodology, totals, and the time-budget impact.

Usage:
    python propose_ma_tables.py 260605_sRCS_WFI_flight_tuning_2hr_APT_upstep.xlsx
"""

import argparse
import os

import pandas as pd
import yaml


YAML_DEFAULT = 'ma_table_ref_revG.yaml'


def load_ma_tables(yaml_path):
    with open(yaml_path) as f:
        doc = yaml.safe_load(f)
    return {entry['ma_table_name']: entry for entry in doc.get('science_tables', {}).values()}


def integration_duration(ma_tables, ma_name, R):
    """Integration time (s) for one exposure with ``R`` resultants of ``ma_name``."""
    return float(ma_tables[ma_name]['integration_duration'][int(R) - 1])


def propose_for_row(R_old, ma_old):
    """
    Return ``(ma_new, R_new, rationale)``.

    See module docstring for the threshold logic.
    """
    R_old = int(R_old)
    if ma_old != 'IM_DIAGNOSTIC':
        return ma_old, R_old, 'kept (not IM_DIAGNOSTIC)'
    if R_old >= 15:
        R_new = max(5, round((R_old + 2) / 3))
        return 'DROP_2_OF_3', R_new, f'DROP_2_OF_3 R={R_new} (~3× fewer stored)'
    if R_old >= 9:
        R_new = max(5, round((R_old + 1) / 2))
        return 'DROP_1_OF_2', R_new, f'DROP_1_OF_2 R={R_new} (~2× fewer stored)'
    return 'IM_DIAGNOSTIC', R_old, 'kept (R<9; saturation or short ramp)'


def renumber_visits_by_ma(df):
    """
    Reassign ``VISIT_NUMBER`` so MA-table boundaries within an original visit
    start a new visit. Sub-visits are numbered 1, 2, 3, … in row order; the
    original ``VISIT_NUMBER`` column is preserved as ``ORIG_VISIT_NUMBER``.
    """
    df = df.copy()
    df['ORIG_VISIT_NUMBER'] = df['VISIT_NUMBER']

    new_visit = []
    cur = 0
    prev_orig = prev_ma = None
    for _, row in df.iterrows():
        orig = row['ORIG_VISIT_NUMBER']
        ma = row['MA_TABLE']
        if orig != prev_orig or ma != prev_ma:
            cur += 1
        new_visit.append(cur)
        prev_orig, prev_ma = orig, ma
    df['VISIT_NUMBER'] = new_visit
    return df


def build_proposal(df, ma_tables):
    """
    Return (df_new, comparison_df).

    ``df_new`` mirrors ``df`` row-for-row with MA_TABLE / R / ACT_TIME /
    VISIT_NUMBER updated, plus an ``ORIG_VISIT_NUMBER`` column preserving the
    original assignment for traceability.
    ``comparison_df`` has per-row before/after for human review.
    """
    df_new = df.copy()
    for i, row in df.iterrows():
        ma_new, R_new, _ = propose_for_row(row['RESULTANTS_PER_EXPOSURE'], row['MA_TABLE'])
        ma_old = row['MA_TABLE']
        R_old = int(row['RESULTANTS_PER_EXPOSURE'])
        T_old = integration_duration(ma_tables, ma_old, R_old)
        T_new = integration_duration(ma_tables, ma_new, R_new)
        # Preserve the per-row overhead (settle, precharge, etc.) that's already
        # baked into ACT_TIME — only the integration portion changes.
        act_old = float(row['ACT_TIME'])
        df_new.at[i, 'MA_TABLE'] = ma_new
        df_new.at[i, 'RESULTANTS_PER_EXPOSURE'] = R_new
        df_new.at[i, 'ACT_TIME'] = round(act_old + (T_new - T_old), 1)

    # Split visits at MA-table boundaries.
    df_new = renumber_visits_by_ma(df_new)

    rows = []
    for i, (row, row_new) in enumerate(zip(df.itertuples(index=False),
                                            df_new.itertuples(index=False))):
        ma_old, R_old = row.MA_TABLE, int(row.RESULTANTS_PER_EXPOSURE)
        ma_new, R_new = row_new.MA_TABLE, int(row_new.RESULTANTS_PER_EXPOSURE)
        T_old = integration_duration(ma_tables, ma_old, R_old)
        T_new = integration_duration(ma_tables, ma_new, R_new)
        led = (row.SRCS_LEDB1 if pd.notna(row.SRCS_LEDB1)
               else row.SRCS_LEDB2 if pd.notna(row.SRCS_LEDB2)
               else 'DARK')
        _, _, note = propose_for_row(R_old, ma_old)
        rows.append({
            'Row': i + 1,
            'Orig_Visit': int(row.VISIT_NUMBER),
            'New_Visit': int(row_new.VISIT_NUMBER),
            'LED': led,
            'MA_old': ma_old,
            'R_old': R_old,
            'T_int_old_s': round(T_old, 2),
            'MA_new': ma_new,
            'R_new': R_new,
            'T_int_new_s': round(T_new, 2),
            'ΔT_s': round(T_new - T_old, 2),
            'ACT_old_s': round(float(row.ACT_TIME), 1),
            'ACT_new_s': float(row_new.ACT_TIME),
            'Stored_frames_saved': R_old - R_new,
            'Rationale': note,
        })
    return df_new, pd.DataFrame(rows)


def build_readme(df_old, df_new, comparison):
    """
    One-line summary rows for the ``README`` sheet.

    Spell out the assumptions so future readers know why we chose these
    thresholds — the right answer depends on whether the user is shot-noise
    or read-noise limited, and what the minimum samples are for the pipeline.
    """
    R_old_total = int(df_old['RESULTANTS_PER_EXPOSURE'].sum())
    R_new_total = int(df_new['RESULTANTS_PER_EXPOSURE'].sum())
    act_old_total = float(df_old['ACT_TIME'].sum())
    act_new_total = float(df_new['ACT_TIME'].sum())

    rows_changed = (df_old['MA_TABLE'].values != df_new['MA_TABLE'].values).sum()
    n_visits_old = df_old['VISIT_NUMBER'].nunique()
    n_visits_new = df_new['VISIT_NUMBER'].nunique()
    rows = [
        ('Source', 'Generalized DROP-table proposal for sRCS LED tuning'),
        ('Generated by', 'propose_ma_tables.py'),
        ('', ''),
        ('SUMMARY', ''),
        ('Rows total',                        len(df_old)),
        ('Rows with MA table changed',        int(rows_changed)),
        ('Visits (original)',                 int(n_visits_old)),
        ('Visits (proposed)',                 int(n_visits_new)),
        ('Stored resultants (original)',      R_old_total),
        ('Stored resultants (proposed)',      R_new_total),
        ('Stored-resultant reduction',        f'{R_old_total - R_new_total} '
                                              f'({(R_old_total - R_new_total) / R_old_total:.1%})'),
        ('Total ACT_TIME (original) [s]',     round(act_old_total, 1)),
        ('Total ACT_TIME (proposed) [s]',     round(act_new_total, 1)),
        ('Wall-clock change [s]',             round(act_new_total - act_old_total, 1)),
        ('', ''),
        ('METHODOLOGY', ''),
        ('Threshold R≥15',                    'IM_DIAGNOSTIC → DROP_2_OF_3, R_new=round((R+2)/3)≥5'),
        ('Threshold 9≤R≤14',                  'IM_DIAGNOSTIC → DROP_1_OF_2, R_new=round((R+1)/2)≥5'),
        ('Threshold R≤8',                     'Keep IM_DIAGNOSTIC (saturation/short ramp; <5 samples after drop)'),
        ('Integration-time matching',         '3R_new − 2 ≈ R_old (DROP_2_OF_3); '
                                              '2R_new − 1 ≈ R_old (DROP_1_OF_2)'),
        ('Minimum samples per ramp',          '5 (preserves UTR slope-fit DOF + jump rejection robustness)'),
        ('Single-MA-per-visit constraint',    'Visits are split at MA-table boundaries and renumbered. '
                                              'Typical pattern: each LED visit splits into '
                                              '(low-flux ramp / mid ramp / saturation) sub-visits.'),
        ('', ''),
        ('ASSUMPTIONS', ''),
        ('Noise regime',                      'LED tuning fluxes are shot-noise dominated above ~3 e-/pix/s; '
                                              'read-noise penalty from fewer samples is small.'),
        ('Per-frame readout',                 '3.16247 s (IR), unchanged across IM_DIAGNOSTIC / DROP_*.'),
        ('Saturation rows (R=3)',             'Untouched — every frame matters near saturation onset.'),
        ('CLEANUP flags',                     'Preserved as-is (only the original visit\'s last row keeps '
                                              'YES). The LED is NOT cycled at intra-visit splits.'),
        ('Per-Observation overhead',          'Not modeled here — APT may add small per-Observation '
                                              'setup time for the extra split visits.'),
        ('', ''),
        ('SHEET GUIDE', ''),
        ('in',                                'Drop-in replacement for the original "in" sheet '
                                              '(includes ORIG_VISIT_NUMBER for traceability).'),
        ('comparison',                        'Per-row before/after, including new visit assignment.'),
        ('README',                            'This sheet.'),
    ]
    return pd.DataFrame(rows, columns=['Field', 'Value'])


def write_output(out_path, df_new, comparison, readme):
    """Write the three sheets, preserving the input's column order in 'in'."""
    with pd.ExcelWriter(out_path, engine='openpyxl') as xl:
        df_new.to_excel(xl, sheet_name='in', index=False)
        comparison.to_excel(xl, sheet_name='comparison', index=False)
        readme.to_excel(xl, sheet_name='README', index=False)


def main():
    parser = argparse.ArgumentParser(
        description='Propose DROP-based MA tables for an sRCS tuning xlsx.',
    )
    parser.add_argument('input', help='Path to the input tuning .xlsx (sheet "in").')
    parser.add_argument('--yaml', default=YAML_DEFAULT,
                        help=f'MA-table reference YAML (default: {YAML_DEFAULT}).')
    parser.add_argument('--out', default=None,
                        help='Output xlsx (default: <input stem>_proposed.xlsx next to input).')
    parser.add_argument('--sheet', default='in', help='Source sheet name (default: in).')
    args = parser.parse_args()

    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.input)),
        os.path.splitext(os.path.basename(args.input))[0] + '_proposed.xlsx',
    )

    ma_tables = load_ma_tables(args.yaml)
    df_old = pd.read_excel(args.input, sheet_name=args.sheet)
    df_new, comparison = build_proposal(df_old, ma_tables)
    readme = build_readme(df_old, df_new, comparison)
    write_output(out_path, df_new, comparison, readme)

    R_old_total = int(df_old['RESULTANTS_PER_EXPOSURE'].sum())
    R_new_total = int(df_new['RESULTANTS_PER_EXPOSURE'].sum())
    print(f'Wrote {out_path}')
    print(f'  Visits: {df_old["VISIT_NUMBER"].nunique()} → {df_new["VISIT_NUMBER"].nunique()} '
          f'(MA-table boundaries trigger splits)')
    print(f'  Stored resultants: {R_old_total} → {R_new_total} '
          f'({(R_old_total - R_new_total) / R_old_total:.1%} reduction)')
    print(f'  ACT_TIME: {df_old["ACT_TIME"].sum():.1f}s → {df_new["ACT_TIME"].sum():.1f}s '
          f'(Δ {df_new["ACT_TIME"].sum() - df_old["ACT_TIME"].sum():+.1f}s)')


if __name__ == '__main__':
    main()
