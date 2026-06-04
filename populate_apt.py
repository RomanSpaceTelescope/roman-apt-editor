"""Populate a Roman APT seed XML with one PassPlan per TPT review CSV.

For each CSV (default: every ``Band*_CFA.csv`` next to the seed, in alphabetical
order), this clones the seed's single ``<PassPlan>`` and fills it with one
``<Observation>`` per ``VISIT_NUMBER`` group, picking between the seed's two
templates:

- the **dark** template (``Calibration/Type = Dark Imaging``) for visits whose
  every row has no LED illumination,
- the **CRNL Direct Illumination** template for every other visit, with its
  ``<LampState>`` filled in from ``rcs_apt_helper.lampstate_for_visit``.

In addition, each Band 1 CSV is reused to derive Band 2 and Band 3 PassPlans by
substituting the LED names (``LED11→LED12/LED13`` on channel B1,
``LED21→LED22/LED23`` on channel B2). All other columns are left as-is.

Each generated PassPlan also gets **three** ``<SurveyPlanStep>`` entries cloned
from the seed's step (each with a fresh 8-char hex uid).

Usage:
    python populate_apt.py                                    # auto-glob next to seed
    python populate_apt.py --csv Band1all_CFA.csv Band6hf_CFA.csv
    python populate_apt.py --seed CFA_seed.apt --out CFA_all_bands.apt
"""

import argparse
import copy
import glob
import os
import re
import secrets
import xml.etree.ElementTree as ET

from rcs_apt_helper import lampstate_for_visit, read_review_csv


NS = 'http://www.stsci.edu/Roman/APT'

SURVEY_STEPS_PER_PASSPLAN = 3


def q(tag):
    """Namespace-qualify an unprefixed element name for ElementTree queries."""
    return f'{{{NS}}}{tag}'


def find_calibration_type(observation):
    """Return the ``<Type>`` element of an Observation's Calibration block."""
    return observation.find(f'{q("SpecialRequirements")}/{q("Calibration")}/{q("Type")}')


def lift_observation_templates(passplan):
    """
    Pull out the dark and calibration ``<Observation>`` templates from a
    PassPlan, then strip every Observation child from it.

    Returns:
    tuple[Element, Element]: (dark_template, calibration_template) — each a
    deep copy untouched by subsequent edits to the live tree.
    """
    dark = calib = None
    for obs in list(passplan.findall(q('Observation'))):
        type_el = find_calibration_type(obs)
        if type_el is None:
            continue
        if type_el.text == 'Dark Imaging':
            dark = copy.deepcopy(obs)
        elif type_el.text == 'CRNL Direct Illumination':
            calib = copy.deepcopy(obs)
        passplan.remove(obs)

    if dark is None or calib is None:
        raise RuntimeError(
            'Seed PassPlan must contain both a "Dark Imaging" and a '
            '"CRNL Direct Illumination" Observation template.'
        )
    return dark, calib


def lift_passplan_template(passplans_container):
    """
    Take the seed's first ``<PassPlan>`` as a deep-copy template, then clear
    every PassPlan child from the container.
    """
    pp = passplans_container.find(q('PassPlan'))
    if pp is None:
        raise RuntimeError('Seed must contain at least one <PassPlan>.')
    template = copy.deepcopy(pp)
    for child in list(passplans_container.findall(q('PassPlan'))):
        passplans_container.remove(child)
    return template


def lift_surveystep_template(surveyplan):
    """
    Take the seed's first ``<SurveyPlanStep>`` as a deep-copy template, then
    clear every step from the SurveyPlan (leaving ``<Links>`` intact).
    """
    step = surveyplan.find(q('SurveyPlanStep'))
    if step is None:
        raise RuntimeError('Seed must contain at least one <SurveyPlanStep>.')
    template = copy.deepcopy(step)
    for s in list(surveyplan.findall(q('SurveyPlanStep'))):
        surveyplan.remove(s)
    return template


# Bands derived from Band 1 by LED-name substitution. Keep the order so PassPlan
# numbering reads Band 1, Band 2, Band 3 within each variant (ALL / HF).
DERIVED_BANDS = (2, 3)


_LABEL_RE = re.compile(r'^Band(\d+)([A-Za-z]+)_CFA\.csv$')


def label_from_csv(csv_path):
    """
    Derive a human-readable PassPlan label from a CSV filename.

    ``Band1all_CFA.csv`` → ``Band 1 ALL``;
    ``Band6hf_CFA.csv``  → ``Band 6 HF``.
    Falls back to the bare stem when the filename doesn't match the pattern.
    """
    name = os.path.basename(csv_path)
    m = _LABEL_RE.match(name)
    if not m:
        return os.path.splitext(name)[0]
    band, suffix = m.group(1), m.group(2)
    return f'Band {band} {suffix.upper()}'


def derive_band(df, source_band, target_band):
    """
    Return a copy of ``df`` with the LED names retargeted from ``source_band``
    to ``target_band``: e.g. ``LED11→LED12``, ``LED21→LED22`` for a Band 1
    table retargeted to Band 2.

    Only the LED-name columns are touched; flux, precharge and timing data are
    intentionally left as-is, per the task spec ("everything stays the same
    except the callouts for the LED").
    """
    out = df.copy()
    for ch in (1, 2):
        col = f'SRCS_LEDB{ch}'
        src = f'LED{ch}{source_band}'
        dst = f'LED{ch}{target_band}'
        out[col] = out[col].replace(src, dst)
    return out


def collect_sources(csv_paths):
    """
    Build the ordered (label, df) list to feed into ``build_passplan``.

    For every ``Band1*_CFA.csv``, also emit derived ``Band 2``/``Band 3``
    sources by LED-name substitution. Other bands pass through unchanged.
    Within each Band 1 input, the derived bands are emitted right after the
    source so PassPlans read 1, 2, 3 grouped per variant.
    """
    sources = []
    for csv_path in csv_paths:
        label = label_from_csv(csv_path)
        df = read_review_csv(csv_path)
        sources.append((label, df))

        m = _LABEL_RE.match(os.path.basename(csv_path))
        if m and int(m.group(1)) == 1:
            suffix = m.group(2).upper()
            for band in DERIVED_BANDS:
                derived_label = f'Band {band} {suffix}'
                sources.append((derived_label, derive_band(df, source_band=1, target_band=band)))
    return sources


def visit_is_dark(visit_rows):
    """A visit is dark iff every row has no LED on either channel."""
    return (visit_rows['SRCS_LEDB1'].isna() & visit_rows['SRCS_LEDB2'].isna()).all()


def build_observation(template, visit_rows, lampstate_text=None):
    """
    Clone ``template`` and overwrite the per-visit fields from ``visit_rows``.

    Sets ``<NumberOfExposures>`` to the visit's total NEXP, ``<MultiAccumTable>``
    and ``<Resultant>`` to the first row's values (per-row R= is already
    encoded inside each LampState line by the helper). For calibration
    observations, replaces the ``<LampState>`` text with ``lampstate_text``.
    """
    obs = copy.deepcopy(template)

    obs.find(q('NumberOfExposures')).text = str(int(visit_rows['NEXP'].sum()))

    first = visit_rows.iloc[0]
    obs.find(q('MultiAccumTable')).text = str(first['MA_TABLE'])
    obs.find(q('Resultant')).text = str(int(first['RESULTANTS_PER_EXPOSURE']))

    if lampstate_text is not None:
        lamp_el = obs.find(f'{q("SpecialRequirements")}/{q("Calibration")}/{q("LampState")}')
        lamp_el.text = lampstate_text

    return obs


def build_passplan(template, number, label, df):
    """
    Clone ``template`` (the seed PassPlan), set its ``Number`` attribute and
    ``<Label>``, and fill it with one ``<Observation>`` per visit in ``df``.

    Returns:
    tuple[Element, int, int]: (passplan, n_dark, n_calib)
    """
    passplan = copy.deepcopy(template)
    passplan.set('Number', str(number))

    label_el = passplan.find(q('Label'))
    if label_el is None:
        # Seed didn't carry a <Label>; insert one at the top of the PassPlan
        # so the order matches the seed's convention (Label, TargetSelection, ...).
        label_el = ET.Element(q('Label'))
        passplan.insert(0, label_el)
    label_el.text = label

    dark_template, calib_template = lift_observation_templates(passplan)

    # New Observations go before any trailing <ToolData/> so the child order
    # stays (TargetSelection, ReferenceCoords, *Observations*, ToolData).
    tool_data = passplan.find(q('ToolData'))
    insert_idx = list(passplan).index(tool_data) if tool_data is not None else len(passplan)

    start_next_exp = 0
    n_dark = n_calib = 0
    for _, visit_rows in df.groupby('VISIT_NUMBER', sort=False):
        if visit_is_dark(visit_rows):
            obs = build_observation(dark_template, visit_rows)
            # A dark visit has no LEDs on any row, so the helper would reset
            # the counter to 0 on every row; mirror that here.
            start_next_exp = 0
            n_dark += 1
        else:
            lines, start_next_exp = lampstate_for_visit(visit_rows, start_next_exp)
            obs = build_observation(calib_template, visit_rows,
                                    lampstate_text='\n'.join(lines))
            n_calib += 1

        passplan.insert(insert_idx, obs)
        insert_idx += 1

    return passplan, n_dark, n_calib


def build_survey_step(template, passplan_number):
    """Clone the seed step with a fresh uid and the right <PassPlan> ref."""
    step = copy.deepcopy(template)
    step.set('uid', secrets.token_hex(4))
    step.find(q('PassPlan')).text = str(passplan_number)
    return step


def populate(seed_path, csv_paths, out_path):
    if not csv_paths:
        raise ValueError('At least one CSV is required.')

    # Keep the seed's prefix-free element names on the way out.
    ET.register_namespace('', NS)

    tree = ET.parse(seed_path)
    root = tree.getroot()

    passplans_container = root.find(q('PassPlans'))
    surveyplan = root.find(q('SurveyPlan'))
    if passplans_container is None or surveyplan is None:
        raise RuntimeError(f'Seed {seed_path} is missing <PassPlans> or <SurveyPlan>.')

    pp_template = lift_passplan_template(passplans_container)
    step_template = lift_surveystep_template(surveyplan)

    sources = collect_sources(csv_paths)

    summaries = []
    for i, (label, df) in enumerate(sources, start=1):
        passplan, n_dark, n_calib = build_passplan(pp_template, i, label, df)
        passplans_container.append(passplan)
        for _ in range(SURVEY_STEPS_PER_PASSPLAN):
            surveyplan.append(build_survey_step(step_template, i))
        summaries.append((i, label, n_dark, n_calib))

    ET.indent(tree, space='    ')
    tree.write(out_path, xml_declaration=True, encoding='UTF-8')

    print(f'Wrote {out_path}:')
    for i, label, n_dark, n_calib in summaries:
        print(f'  PassPlan {i} "{label}": '
              f'{n_dark} dark + {n_calib} calibration = {n_dark + n_calib} obs '
              f'+ {SURVEY_STEPS_PER_PASSPLAN} survey steps')
    total_steps = SURVEY_STEPS_PER_PASSPLAN * len(summaries)
    print(f'  Total: {len(summaries)} PassPlans, {total_steps} SurveyPlanSteps.')


def default_csv_paths(seed_path):
    """Glob ``Band*_CFA.csv`` from the seed's directory, alphabetically."""
    seed_dir = os.path.dirname(os.path.abspath(seed_path))
    return sorted(glob.glob(os.path.join(seed_dir, 'Band*_CFA.csv')))


def main():
    parser = argparse.ArgumentParser(
        description='Populate a Roman APT seed XML with one PassPlan per TPT review CSV.',
    )
    parser.add_argument('--seed', default='CFA_seed.apt',
                        help='Path to the seed APT XML file (default: CFA_seed.apt).')
    parser.add_argument('--csv', nargs='+', default=None,
                        help='CSV files, in PassPlan order. Defaults to Band*_CFA.csv next to the seed.')
    parser.add_argument('--out', default='CFA_all_bands.apt',
                        help='Path to write the populated APT XML (default: CFA_all_bands.apt).')
    args = parser.parse_args()

    csv_paths = args.csv if args.csv else default_csv_paths(args.seed)
    if not csv_paths:
        parser.error(f'No CSVs found next to seed {args.seed} matching Band*_CFA.csv.')
    populate(args.seed, csv_paths, args.out)


if __name__ == '__main__':
    main()
