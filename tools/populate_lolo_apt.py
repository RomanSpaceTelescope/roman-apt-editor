"""Populate a Roman APT seed XML for LOLO observations with target and fiducial overrides.

Input is a review table (CSV/XLSX) with columns:
- PASSPLAN_LABEL: PassPlan label grouping
- OBSERVATION_NUMBER: Observation sequence within PassPlan
- OBSERVATION_TYPE: "dark", "sky", or "lolo"
- TARGET: Target number or "NONE"
- OPTICAL_ELEMENT: Filter name (e.g., "F087", "DARK")
- FIDUCIAL_APERTURE: Aperture name (e.g., "WFI01_FULL") or empty
- All CFA/tuning columns: VISIT_NUMBER, NEXP, RESULTANTS_PER_EXPOSURE, MA_TABLE,
  SRCS_LEDB1/2, fluxes, precharge params, cleanup flags

For dark/lit observations, one row per observation.
For CRNL observations, multiple rows grouped by (PASSPLAN_LABEL, OBSERVATION_NUMBER),
with LampState auto-generated from LED columns via lampstate_for_visit.

Usage:
    python populate_lolo_apt.py --seed LOLO_seed.apt --input tuning_lolo.xlsx --out tuning_lolo.apt
    python populate_lolo_apt.py --seed LOLO_seed.apt --input tuning_lolo.xlsx --sheet "LOLO" --out tuning_lolo.apt
"""

import argparse
import copy
import os
import secrets
import xml.etree.ElementTree as ET

import pandas as pd

from helpers.rcs_apt_helper import read_review_table, lampstate_for_visit


NS = 'http://www.stsci.edu/Roman/APT'


def q(tag):
    """Namespace-qualify an unprefixed element name for ElementTree queries."""
    return f'{{{NS}}}{tag}'


def find_calibration_type(observation):
    """Return the ``<Type>`` element of an Observation's Calibration block."""
    return observation.find(f'{q("SpecialRequirements")}/{q("Calibration")}/{q("Type")}')


def lift_observation_templates_lolo(passplan):
    """
    Pull out dark, sky, and lolo observation templates from a PassPlan,
    then strip every Observation child from it.

    Expects exactly 3 observations:
    - Dark: Calibration/Type = "Dark Imaging"
    - Sky: no Calibration element
    - Lolo: Calibration/Type contains "CRNL" or "LOLO"

    Returns:
    tuple[Element, Element, Element]: (dark_template, sky_template, lolo_template)
    """
    dark = sky = lolo = None
    for obs in list(passplan.findall(q('Observation'))):
        type_el = find_calibration_type(obs)
        if type_el is not None:
            if type_el.text == 'Dark Imaging':
                dark = copy.deepcopy(obs)
            elif 'CRNL' in type_el.text or 'LOLO' in type_el.text:
                lolo = copy.deepcopy(obs)
        else:
            # No calibration type => sky observation
            sky = copy.deepcopy(obs)
        passplan.remove(obs)

    if dark is None or sky is None or lolo is None:
        raise RuntimeError(
            'Seed PassPlan must contain exactly 3 Observation templates: '
            '"Dark Imaging", a sky observation (no Calibration), and a LOLO observation.'
        )
    return dark, sky, lolo


def lift_passplan_template(passplans_container):
    """Take the seed's first PassPlan as a deep-copy template, then clear PassPlans."""
    pp = passplans_container.find(q('PassPlan'))
    if pp is None:
        raise RuntimeError('Seed must contain at least one <PassPlan>.')
    template = copy.deepcopy(pp)
    for child in list(passplans_container.findall(q('PassPlan'))):
        passplans_container.remove(child)
    return template


def lift_surveystep_template(surveyplan):
    """Take the seed's first SurveyPlanStep as a deep-copy template, then clear steps."""
    step = surveyplan.find(q('SurveyPlanStep'))
    if step is None:
        raise RuntimeError('Seed must contain at least one <SurveyPlanStep>.')
    template = copy.deepcopy(step)
    for s in list(surveyplan.findall(q('SurveyPlanStep'))):
        surveyplan.remove(s)
    return template


def read_lolo_table(path, sheet=None):
    """
    Read LOLO input table and validate required LOLO-specific columns.

    Extends read_review_table (which validates CFA/tuning columns) with
    LOLO-specific columns: PASSPLAN_LABEL, OBSERVATION_NUMBER, OBSERVATION_TYPE,
    TARGET, OPTICAL_ELEMENT, FIDUCIAL_APERTURE.
    """
    df = read_review_table(path, sheet=sheet)

    lolo_cols = {
        'PASSPLAN_LABEL', 'OBSERVATION_NUMBER', 'OBSERVATION_TYPE',
        'TARGET', 'OPTICAL_ELEMENT', 'FIDUCIAL_APERTURE',
    }
    missing = lolo_cols - set(df.columns)
    if missing:
        raise ValueError(
            f'Missing required LOLO columns in {path}: {sorted(missing)}'
        )

    return df


def build_observation_lolo(obs_type, target, optical_element, obs_rows,
                           dark_template, sky_template, lolo_template,
                           fiducial_aperture=None):
    """
    Build a LOLO observation from the appropriate template and row data.

    Fills in Target, OpticalElement, NumberOfExposures, MultiAccumTable, Resultant.
    For lolo, generates LampState from obs_rows via lampstate_for_visit.
    Adds FiducialPointOverride if fiducial_aperture is specified.

    Parameters:
    obs_type (str): "dark", "sky", or "lolo"
    target (str): Target number or "NONE"
    optical_element (str): Filter name
    obs_rows (pd.DataFrame): Rows for this observation (1+ rows for lolo, 1 for dark/sky)
    dark_template, sky_template, lolo_template (Element): Observation templates
    fiducial_aperture (str, optional): Aperture name or None

    Returns:
    Element: The built observation
    """
    obs_type = obs_type.lower().strip()

    if obs_type == 'dark':
        obs = copy.deepcopy(dark_template)
    elif obs_type == 'sky':
        obs = copy.deepcopy(sky_template)
    elif obs_type == 'lolo':
        obs = copy.deepcopy(lolo_template)
    else:
        raise ValueError(f'Unknown observation type: {obs_type}')

    # Set Target
    target_el = obs.find(q('Target'))
    if target_el is not None:
        target_el.text = str(target)

    # Set OpticalElement
    opt_el = obs.find(q('OpticalElement'))
    if opt_el is not None:
        opt_el.text = str(optical_element)

    # Set NumberOfExposures (sum of NEXP across rows)
    nexp_total = int(obs_rows['NEXP'].sum())
    nexp_el = obs.find(q('NumberOfExposures'))
    if nexp_el is not None:
        nexp_el.text = str(nexp_total)

    # Set MultiAccumTable and Resultant from first row
    first_row = obs_rows.iloc[0]
    ma_el = obs.find(q('MultiAccumTable'))
    if ma_el is not None:
        ma_el.text = str(first_row['MA_TABLE'])

    res_el = obs.find(q('Resultant'))
    if res_el is not None:
        res_el.text = str(int(first_row['RESULTANTS_PER_EXPOSURE']))

    # Generate LampState for lolo observations
    if obs_type == 'lolo':
        lines, _ = lampstate_for_visit(obs_rows, start_next_exp=0)
        lamp_el = obs.find(f'{q("SpecialRequirements")}/{q("Calibration")}/{q("LampState")}')
        if lamp_el is not None:
            lamp_el.text = '\n'.join(lines) if lines else ''

    # Add or update FiducialPointOverride if specified
    if fiducial_aperture:
        spec_req = obs.find(q('SpecialRequirements'))
        if spec_req is None:
            spec_req = ET.Element(q('SpecialRequirements'))
            obs.append(spec_req)

        fid_override = spec_req.find(q('FiducialPointOverride'))
        if fid_override is None:
            fid_override = ET.Element(q('FiducialPointOverride'))
            spec_req.append(fid_override)

        fid_aperture_el = fid_override.find(q('FiducialPointOverrideAperture'))
        if fid_aperture_el is None:
            fid_aperture_el = ET.Element(q('FiducialPointOverrideAperture'))
            fid_override.append(fid_aperture_el)

        fid_aperture_el.text = str(fiducial_aperture)

    return obs


def build_passplan_lolo(pp_template, pp_number, pp_label, pp_df,
                        dark_template, sky_template, lolo_template):
    """
    Build a LOLO PassPlan from grouped rows.

    Groups rows by OBSERVATION_NUMBER, sorts by that number, builds one
    observation per group, and inserts into the PassPlan.

    Returns:
    tuple[Element, int, int, int]: (passplan, n_dark, n_sky, n_lolo)
    """
    passplan = copy.deepcopy(pp_template)
    passplan.set('Number', str(pp_number))

    label_el = passplan.find(q('Label'))
    if label_el is None:
        label_el = ET.Element(q('Label'))
        passplan.insert(0, label_el)
    label_el.text = str(pp_label)

    # Insert point for new observations (before ToolData)
    tool_data = passplan.find(q('ToolData'))
    insert_idx = list(passplan).index(tool_data) if tool_data is not None else len(passplan)

    n_dark = n_sky = n_lolo = 0

    # Group by OBSERVATION_NUMBER, sort by that number
    for obs_num in sorted(pp_df['OBSERVATION_NUMBER'].unique()):
        obs_rows = pp_df[pp_df['OBSERVATION_NUMBER'] == obs_num].copy()
        obs_rows = obs_rows.sort_values('VISIT_NUMBER', ascending=True)

        # All rows in this group should have the same OBSERVATION_TYPE, TARGET, OPTICAL_ELEMENT
        obs_type = obs_rows.iloc[0]['OBSERVATION_TYPE']
        target = obs_rows.iloc[0]['TARGET']
        optical_elem = obs_rows.iloc[0]['OPTICAL_ELEMENT']
        fiducial_ap = obs_rows.iloc[0]['FIDUCIAL_APERTURE']

        # Handle NaN/None/empty values
        if pd.isna(fiducial_ap) or fiducial_ap == '':
            fiducial_ap = None

        obs = build_observation_lolo(
            obs_type=obs_type,
            target=target,
            optical_element=optical_elem,
            obs_rows=obs_rows,
            dark_template=dark_template,
            sky_template=sky_template,
            lolo_template=lolo_template,
            fiducial_aperture=fiducial_ap,
        )

        passplan.insert(insert_idx, obs)
        insert_idx += 1

        obs_type_lower = obs_type.lower().strip()
        if obs_type_lower == 'dark':
            n_dark += 1
        elif obs_type_lower == 'sky':
            n_sky += 1
        elif obs_type_lower == 'lolo':
            n_lolo += 1

    return passplan, n_dark, n_sky, n_lolo


def build_survey_step(template, passplan_number):
    """Clone the seed step with a fresh uid and the right PassPlan ref."""
    step = copy.deepcopy(template)
    step.set('uid', secrets.token_hex(4))
    step.find(q('PassPlan')).text = str(passplan_number)
    return step


def populate_lolo(seed_path, input_path, out_path, sheet=None):
    """Main population function."""
    ET.register_namespace('', NS)

    tree = ET.parse(seed_path)
    root = tree.getroot()

    passplans_container = root.find(q('PassPlans'))
    surveyplan = root.find(q('SurveyPlan'))
    if passplans_container is None or surveyplan is None:
        raise RuntimeError(f'Seed {seed_path} is missing <PassPlans> or <SurveyPlan>.')

    pp_template = lift_passplan_template(passplans_container)
    step_template = lift_surveystep_template(surveyplan)
    dark_template, sky_template, lolo_template = lift_observation_templates_lolo(pp_template)

    df = read_lolo_table(input_path, sheet=sheet)

    summaries = []
    for pp_num, pp_label in enumerate(df['PASSPLAN_LABEL'].unique(), start=1):
        pp_df = df[df['PASSPLAN_LABEL'] == pp_label]
        passplan, n_dark, n_sky, n_lolo = build_passplan_lolo(
            pp_template, pp_num, pp_label, pp_df,
            dark_template, sky_template, lolo_template
        )
        passplans_container.append(passplan)
        surveyplan.append(build_survey_step(step_template, pp_num))
        summaries.append((pp_num, pp_label, n_dark, n_sky, n_lolo))

    ET.indent(tree, space='    ')
    tree.write(out_path, xml_declaration=True, encoding='UTF-8')

    print(f'Wrote {out_path}:')
    for pp_num, pp_label, n_dark, n_sky, n_lolo in summaries:
        total_obs = n_dark + n_sky + n_lolo
        print(f'  PassPlan {pp_num} "{pp_label}": '
              f'{n_dark} dark + {n_sky} sky + {n_lolo} lolo = {total_obs} obs + 1 survey step')
    total_steps = len(summaries)
    print(f'  Total: {len(summaries)} PassPlans, {total_steps} SurveyPlanSteps.')


def main():
    parser = argparse.ArgumentParser(
        description='Populate a Roman APT seed XML for LOLO observations.'
    )
    parser.add_argument('--seed', required=True,
                        help='Path to the LOLO seed APT XML file.')
    parser.add_argument('--input', required=True,
                        help='Path to the LOLO input table (CSV/XLSX/XLSM).')
    parser.add_argument('--sheet', default=None,
                        help='Sheet name for Excel inputs (default: "in", or the first sheet).')
    parser.add_argument('--out', required=True,
                        help='Path to write the populated APT XML.')
    args = parser.parse_args()

    populate_lolo(args.seed, args.input, args.out, sheet=args.sheet)


if __name__ == '__main__':
    main()
