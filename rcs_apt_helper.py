"""Construct APT strings from a TPT review CSV for RCS tests.

Usage:
    python rcs_apt_helper.py path/to/review.csv
    python rcs_apt_helper.py path/to/review.xlsx --sheet in
"""

import argparse
import os

import numpy as np
import pandas as pd


def generate_output(Nexp, LED1=None, flux1=None, precharge_duration1=None, precharge_flux1=None,
                    LED2=None, flux2=None, precharge_duration2=None, precharge_flux2=None):
    """
    Construct APT strings based on TPT review table for RCS tests.

    Parameters:
    Nexp (int): Number of exposures
    LED1 (str, optional): Name of the first LED
    flux1 (float, optional): Flux value for the first LED
    precharge_duration1 (float, optional): Precharge duration for the first LED
    precharge_flux1 (float, optional): Precharge flux for the first LED
    LED2 (str, optional): Name of the second LED
    flux2 (float, optional): Flux value for the second LED
    precharge_duration2 (float, optional): Precharge duration for the second LED
    precharge_flux2 (float, optional): Precharge flux for the second LED

    Returns:
    None
    """
    for i in range(Nexp):
        fmt = f'{i+1}'
        if LED1 is not None:
            fmt += f', {LED1}={flux1}'
        if (precharge_duration1 is not None) and (not np.isnan(precharge_duration1)) and (i == 0):
            fmt += f' (pre={int(precharge_duration1)},{precharge_flux1})'
        if LED2 is not None:
            fmt += f', {LED2}={flux2}'
        if (precharge_duration2 is not None) and (not np.isnan(precharge_duration2)) and (i == 0):
            fmt += f' (pre={int(precharge_duration2)},{precharge_flux2})'

        print(fmt)


def format_flight_lines(Nexp, start_exp=0, nres=None,
                        LED1=None, flux1=None, precharge_duration1=None, precharge_flux1=None,
                        LED2=None, flux2=None, precharge_duration2=None, precharge_flux2=None):
    """
    Build the per-exposure APT lines for one TPT review row, without printing.

    Same parameter semantics as ``generate_output_flight``; returns the list
    of formatted strings instead of writing them to stdout, so callers that
    need to embed the lines into structured output (e.g. an APT XML
    ``<LampState>`` block) can do so.

    Returns:
    list[str]: One string per exposure, in order.
    """
    lines = []
    for i in range(Nexp):
        fmt = f'{start_exp+i+1}'
        if nres is not None:
            fmt += f', R={int(nres)}'
        if LED1 is not None:
            fmt += f', {LED1}={flux1}'
        if (precharge_duration1 is not None) and (not np.isnan(precharge_duration1)) and (i == 0):
            fmt += f' (pre={int(precharge_duration1)},{precharge_flux1})'
        if LED2 is not None:
            fmt += f', {LED2}={flux2}'
        if (precharge_duration2 is not None) and (not np.isnan(precharge_duration2)) and (i == 0):
            fmt += f' (pre={int(precharge_duration2)},{precharge_flux2})'

        lines.append(fmt)
    return lines


def generate_output_flight(Nexp, start_exp=0, nres=None,
                           LED1=None, flux1=None, precharge_duration1=None, precharge_flux1=None,
                           LED2=None, flux2=None, precharge_duration2=None, precharge_flux2=None):
    """
    Construct APT strings based on TPT review table for RCS tests.

    This version remembers the start number to combine TPT activities into single APT observations.

    Parameters:
    Nexp (int): Number of exposures
    start_exp (int, optional): Starting experiment number (default is 0)
    nres (int, optional): Number of resultants for this row; emitted as `R=<nres>` next to the exposure number.
    LED1 (str, optional): Name of the first LED
    flux1 (float, optional): Flux value for the first LED
    precharge_duration1 (float, optional): Precharge duration for the first LED
    precharge_flux1 (float, optional): Precharge flux for the first LED
    LED2 (str, optional): Name of the second LED
    flux2 (float, optional): Flux value for the second LED
    precharge_duration2 (float, optional): Precharge duration for the second LED
    precharge_flux2 (float, optional): Precharge flux for the second LED

    Returns:
    int: The next exposure number
    """
    for line in format_flight_lines(
        Nexp, start_exp=start_exp, nres=nres,
        LED1=LED1, flux1=flux1,
        precharge_duration1=precharge_duration1, precharge_flux1=precharge_flux1,
        LED2=LED2, flux2=flux2,
        precharge_duration2=precharge_duration2, precharge_flux2=precharge_flux2,
    ):
        print(line)
    return start_exp + Nexp


def _cleanup_fires(value):
    """Treat ``'YES'`` (case-insensitive) as cleanup; blank/None/``'NO'`` as not."""
    return value is not None and str(value).strip().upper() == 'YES'


def lampstate_for_visit(visit_rows, start_next_exp):
    """
    Build the lines for one APT visit's ``<LampState>`` block.

    Iterates the CSV rows belonging to a single visit, emitting per-row
    exposure lines via ``format_flight_lines`` and threading the running
    exposure counter the same way ``process_csv`` does — including resetting
    to 0 whenever an LED's ``WFI_SRCS_LED*_CLEANUP`` flag fires (``YES``), and
    resetting when a row has no LEDs at all.

    Parameters:
    visit_rows (pandas.DataFrame): Rows for this visit (already filtered).
    start_next_exp (int): Counter carried over from the previous visit.

    Returns:
    tuple[list[str], int]: (lines for this visit, updated counter for the next).
    """
    lines = []
    for _, row in visit_rows.iterrows():
        LEDB1 = row['SRCS_LEDB1']
        LEDB2 = row['SRCS_LEDB2']
        LEDB1_FLUX = round(row['SRCS_LEDB1_FLUX'], 2)
        LEDB2_FLUX = round(row['SRCS_LEDB2_FLUX'], 2)
        LEDB1_PREFLUX = row['SRCS_LEDB1_PRECHARGE_FLUX']
        LEDB1_PREDUR = row['SRCS_LEDB1_PRECHARGE_DURATION']
        LEDB2_PREFLUX = row['SRCS_LEDB2_PRECHARGE_FLUX']
        LEDB2_PREDUR = row['SRCS_LEDB2_PRECHARGE_DURATION']
        EXTING_LED1 = row['WFI_SRCS_LEDB1_CLEANUP']
        EXTING_LED2 = row['WFI_SRCS_LEDB2_CLEANUP']

        NEXP = row['NEXP']
        NRES = row['RESULTANTS_PER_EXPOSURE']

        lines.extend(format_flight_lines(
            NEXP, start_exp=start_next_exp, nres=NRES,
            LED1=LEDB1, flux1=LEDB1_FLUX,
            precharge_duration1=LEDB1_PREDUR, precharge_flux1=LEDB1_PREFLUX,
            LED2=LEDB2, flux2=LEDB2_FLUX,
            precharge_duration2=LEDB2_PREDUR, precharge_flux2=LEDB2_PREFLUX,
        ))
        current_exp_num = start_next_exp + NEXP

        if LEDB1 is not None:
            start_next_exp = 0 if _cleanup_fires(EXTING_LED1) else current_exp_num
        if LEDB2 is not None:
            start_next_exp = 0 if _cleanup_fires(EXTING_LED2) else current_exp_num
        if LEDB1 is None and LEDB2 is None:
            start_next_exp = 0

    return lines, start_next_exp


_NA_VALUES = ['', ' ', 'NaN', 'nan']


def read_review_table(path, sheet=None):
    """
    Read a TPT review table from CSV or Excel, with NaN → None.

    For ``.xlsx``/``.xls`` inputs, defaults to a sheet named ``in`` if present,
    otherwise the first sheet. Pass ``sheet`` to pick a different one.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.xlsx', '.xls', '.xlsm'):
        if sheet is None:
            sheet = 'in' if 'in' in pd.ExcelFile(path).sheet_names else 0
        df = pd.read_excel(path, sheet_name=sheet, na_values=_NA_VALUES)
    else:
        df = pd.read_csv(path, na_values=_NA_VALUES)
    return df.where(pd.notna(df), None)


def read_review_csv(file):
    """Read a TPT review CSV the way ``process_csv`` does, with NaN→None."""
    return read_review_table(file)


def process_csv(file, sheet=None):
    df = read_review_table(file, sheet=sheet)

    # Group rows by visit so we emit one header per visit and bundle all of
    # that visit's activity lines underneath.
    start_next_exp = 0
    for activity, visit_rows in df.groupby('VISIT_NUMBER', sort=False):
        # MA table is per-row but typically constant within a visit; show the
        # set if it ever varies so we don't silently hide that.
        ma_tables = visit_rows['MA_TABLE'].dropna().unique().tolist()
        ma_label = ma_tables[0] if len(ma_tables) == 1 else '/'.join(map(str, ma_tables))
        print(f'===== Visit {activity} - {ma_label} =====')

        lines, start_next_exp = lampstate_for_visit(visit_rows, start_next_exp)
        for line in lines:
            print(line)


def main():
    parser = argparse.ArgumentParser(
        description='Generate APT exposure strings from a TPT review table (CSV or XLSX).',
    )
    parser.add_argument('file', help='Path to the TPT review table (.csv or .xlsx).')
    parser.add_argument('--sheet', default=None,
                        help='For XLSX inputs, the sheet name to read (default: "in", or the first sheet).')
    args = parser.parse_args()
    process_csv(args.file, sheet=args.sheet)


if __name__ == '__main__':
    main()
