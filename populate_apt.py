"""Populate a Roman APT seed XML with one PassPlan per TPT review table.

For each input table (CSV, XLSX, XLS, or XLSM; default: every ``Band*_CFA.*``
next to the seed, in alphabetical order), this clones the seed's single
``<PassPlan>`` and fills it with one ``<Observation>`` per ``VISIT_NUMBER``
group, picking between the seed's two templates:

- the **dark** template (``Calibration/Type = Dark Imaging``) for visits whose
  every row has no LED illumination,
- the **CRNL Direct Illumination** template for every other visit, with its
  ``<LampState>`` filled in from ``rcs_apt_helper.lampstate_for_visit``.

When **multiple** Band-keyed inputs are passed, each ``Band1*_CFA.*`` is reused
to derive Band 2 and Band 3 PassPlans by LED-name substitution
(``LED11→LED12/LED13`` on channel B1, ``LED21→LED22/LED23`` on channel B2).
A **single-input** run skips that derivation: the table maps to exactly one
PassPlan.

Each generated PassPlan also gets **three** ``<SurveyPlanStep>`` entries cloned
from the seed's step (each with a fresh 8-char hex uid).

Usage:
    python populate_apt.py                                    # auto-glob next to seed
    python populate_apt.py --input Band1all_CFA.csv Band6hf_CFA.xlsx
    python populate_apt.py --input 260615_sRCS_WFI_flight_tuning_CFA_APT.xlsx --seed tuning_seed.apt --out tuning.apt
    python populate_apt.py --input data.xlsx --sheet 'Results' --seed tuning_seed.apt --out tuning.apt
"""

import argparse
import copy
import glob
import os
import re
import secrets
import xml.etree.ElementTree as ET

from rcs_apt_helper import lampstate_for_visit, read_review_table


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
    Pull out the dark and lit-calibration ``<Observation>`` templates from a
    PassPlan, then strip every Observation child from it.

    The dark template is identified by ``Calibration/Type = Dark Imaging``;
    the other Observation in the seed PassPlan is taken as the lit template
    regardless of its calibration type (e.g. ``CRNL Direct Illumination`` for
    the CFA seed, ``Internal Flat`` for the tuning seed).

    Returns:
    tuple[Element, Element]: (dark_template, calibration_template) — each a
    deep copy untouched by subsequent edits to the live tree.
    """
    dark = calib = None
    for obs in list(passplan.findall(q('Observation'))):
        type_el = find_calibration_type(obs)
        if type_el is not None and type_el.text == 'Dark Imaging':
            dark = copy.deepcopy(obs)
        else:
            calib = copy.deepcopy(obs)
        passplan.remove(obs)

    if dark is None or calib is None:
        raise RuntimeError(
            'Seed PassPlan must contain a "Dark Imaging" Observation template '
            'and one other Observation template for lit visits.'
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


_LABEL_RE = re.compile(r'^Band(\d+)([A-Za-z]+)_CFA\.(?:csv|xlsx?|xlsm)$')


def label_from_input(input_path):
    """
    Derive a human-readable PassPlan label from an input filename.

    ``Band1all_CFA.csv`` → ``Band 1 ALL``;
    ``Band6hf_CFA.csv``  → ``Band 6 HF``.
    Falls back to the bare stem when the filename doesn't match the pattern.
    """
    name = os.path.basename(input_path)
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


def collect_sources(input_paths, sheet=None):
    """
    Build the ordered (label, df) list to feed into ``build_passplan``.

    With **multiple** inputs, every ``Band1*_CFA.*`` is followed by derived
    ``Band 2``/``Band 3`` sources via LED-name substitution, so PassPlans read
    1, 2, 3 grouped per variant. A **single-input** run skips that derivation
    — the table maps 1-to-1 to a single PassPlan, regardless of filename.
    """
    sources = []
    derive = len(input_paths) > 1
    for path in input_paths:
        label = label_from_input(path)
        df = read_review_table(path, sheet=sheet)
        sources.append((label, df))

        if not derive:
            continue
        m = _LABEL_RE.match(os.path.basename(path))
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

    n_dark = n_calib = 0
    for _, visit_rows in df.groupby('VISIT_NUMBER', sort=False):
        # Each new visit is its own APT <Observation> with its own <LampState>;
        # exposure numbering restarts at 1 every visit, regardless of the
        # previous visit's cleanup flag. (In the CFA CSVs every visit ends with
        # CLEANUP=YES so this was already the de-facto behavior; making it
        # explicit also handles MA-split sub-visits where intermediate
        # CLEANUP=NO would otherwise chain the counter across observations.)
        if visit_is_dark(visit_rows):
            obs = build_observation(dark_template, visit_rows)
            n_dark += 1
        else:
            lines, _ = lampstate_for_visit(visit_rows, 0)
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


def populate(seed_path, input_paths, out_path, sheet=None):
    if not input_paths:
        raise ValueError('At least one input table is required.')

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

    sources = collect_sources(input_paths, sheet=sheet)

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


def default_input_paths(seed_path):
    """
    Glob ``Band*_CFA.csv`` or ``Band*_CFA.xlsx`` from the seed's directory,
    alphabetically. CSVs are preferred if both formats exist for the same band.
    """
    seed_dir = os.path.dirname(os.path.abspath(seed_path))
    # Glob both CSV and XLSX files, sorted by filename
    all_files = sorted(
        glob.glob(os.path.join(seed_dir, 'Band*_CFA.csv')) +
        glob.glob(os.path.join(seed_dir, 'Band*_CFA.xlsx')) +
        glob.glob(os.path.join(seed_dir, 'Band*_CFA.xls')) +
        glob.glob(os.path.join(seed_dir, 'Band*_CFA.xlsm'))
    )
    # Deduplicate: if both Band1_CFA.csv and Band1_CFA.xlsx exist,
    # keep only the CSV (prefer CSV order: .csv < .xlsx < .xls < .xlsm alphabetically).
    seen_bases = {}
    result = []
    for path in all_files:
        base = os.path.splitext(os.path.basename(path))[0]
        if base not in seen_bases:
            seen_bases[base] = path
            result.append(path)
    return sorted(result)


def main():
    parser = argparse.ArgumentParser(
        description='Populate a Roman APT seed XML with one PassPlan per TPT review table.',
    )
    parser.add_argument('--seed', default='CFA_seed.apt',
                        help='Path to the seed APT XML file (default: CFA_seed.apt).')
    parser.add_argument('--input', '--csv', dest='input', nargs='+', default=None,
                        help='Review tables (CSV, XLSX, XLS, or XLSM), in PassPlan order. '
                             'Defaults to Band*_CFA.* next to the seed (alphabetically).')
    parser.add_argument('--sheet', default=None,
                        help='Sheet name for Excel inputs (default: "in", or the first sheet). '
                             'Ignored for CSV inputs.')
    parser.add_argument('--out', default='CFA_all_bands.apt',
                        help='Path to write the populated APT XML (default: CFA_all_bands.apt).')
    args = parser.parse_args()

    input_paths = args.input if args.input else default_input_paths(args.seed)
    if not input_paths:
        parser.error(f'No inputs supplied and no Band*_CFA.* found next to seed {args.seed}.')
    populate(args.seed, input_paths, args.out, sheet=args.sheet)


if __name__ == '__main__':
    main()
