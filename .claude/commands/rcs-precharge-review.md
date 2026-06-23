---
name: rcs-precharge-review
description: Review or correct Roman WFI sRCS LED precharge in an APT flux sequence (LOLO, tuning, CFA, etc.). Use when a user shares an APT/RCS sequence CSV (columns like SRCS_LEDB1_FLUX / _PRECHARGE_FLUX / _PRECHARGE_DURATION) and asks to check, audit, validate, fix, or generate the precharge / "credit" numbers. Knows the precharge charge model, step-aware credit, the 300 s duration cap, the LED max-current (~96 mA) → extend-time behavior, the 10-minute decay rule, and the common calculator bugs.
---

# RCS Precharge Review

Audit (or correct) the precharge flux/duration columns of a Roman WFI sRCS APT
sequence. The physics and the validated calibration are baked into
`scripts/check_rcs_precharge.py` — run it first, then explain the findings.

## When this applies
A CSV with per-LED columns such as `SRCS_LEDB1`, `SRCS_LEDB1_FLUX`,
`SRCS_LEDB1_PRECHARGE_DURATION`, `SRCS_LEDB1_PRECHARGE_FLUX`,
`WFI_SRCS_LEDB1_CLEANUP` (and a `B2` set), plus grouping columns
`PASSPLAN_LABEL`, `OBSERVATION_NUMBER`, `OBSERVATION_TYPE`. LEDs are named
`LED{bank}{band}` (e.g. `LED14` = bank 1, band 4).

## How to run
```
python scripts/check_rcs_precharge.py SEQUENCE.csv --cal CALIBRATION.xlsx --side B
```
- `--cal` is the calibration workbook (needs sheets `H4RG Flux Calibration` and
  `LED Calibration`). Use the authoritative **260401** ("4/01") workbook.
- `--side B` → box 2 (Side B / Flight-2); `--side A` → box 1.
- `--fix OUT.csv` writes a corrected sequence (only the two precharge columns change).
- `--all` shows every IR row; default shows only flagged ones.
- Exit code is non-zero if anything is flagged (handy for CI).

The script recomputes the correct credited precharge for every IR row, diffs it
against the file, and classifies each problem. After running, summarize the
findings in plain language and, if asked, run with `--fix` and hand back the
corrected file.

## The model (what "correct" means)
See `reference.md` for the full write-up. In brief:

1. **Only IR bands 4–6 precharge.** Visible bands 1–3 must be blank.
2. **Charge model.** Settling an LED needs a fixed charge
   `Q = pc_band · ΔI`, where `ΔI = I_target − I_prev` (currents in mA) and
   `pc` is the per-band constant. Precharge delivers it as `Q = I_pc · t_pc`.
3. **Default duration 30 s**, solve for current. If that current exceeds the LED
   **max (~96 mA)**, pin current at the max and **extend the time**
   (`t = Q / I_max`). Hard cap **t ≤ 300 s** (a point needing >300 s is invalid).
4. **Credit.** Within one sweep the LED stays lit, so each up-step credits the
   previous equilibrium (`I_prev`), not dark. The **first** step of a sweep is
   from dark. A sweep = consecutive rows with the same
   `(PASSPLAN_LABEL, OBSERVATION_NUMBER, LED)`.
5. **One credit, two knobs — spent in a fixed priority.** The credit (smaller
   `ΔI`) reduces the required charge `Q`. How it's spent depends on the regime,
   and the priority is:
   - **At the current ceiling (clamped): save TIME first.** Keep current pinned
     at max (~96 mA) and let the extended duration shrink as credit grows —
     down toward the **30 s floor**.
   - **Once the duration reaches 30 s: switch to saving CURRENT.** Hold duration
     at 30 s and let the current drop below max with any further credit.
   So duration never goes below 30 s, current never goes above max, and you
   never reduce the flux *and* keep the long from-dark time. (This is automatic
   in the script: it tries 30 s first, clamps to max only when 30 s is
   infeasible, and recomputes as credit changes.)
6. **10-minute decay.** Once an LED is off it decays to cold; after ~10 min off
   it's fully discharged → next use starts from dark. `CLEANUP=YES` flags that
   boundary. If the same LED is re-fired **within** ~10 min, it still holds
   partial charge and could be credited further (the script notes this).

## Calibration & constants (authoritative)
- **Flux cal must be looked up by BOTH band AND bank.** Keying by band alone
  silently uses bank 2 for every LED — the #1 bug to check for.
- **Precharge constants** [mA·s/mA]: Side B `{4:2164, 5:1300, 6:1040}`,
  Side A `{4:2164, 5:1633, 6:1040}`. Do **not** use the workbook's `Precharge`
  sheet `{1665,1000,800}` — that's a stale 2024-03-19 baseline (~23 % low).
- Max LED current ≈ **95.9 mA** (top of the high-range table; code cap
  `2·65535−1`).

## What the checker flags (and how to phrase it)
- **MISSING / present-on-visible** — precharge blank on an IR band, or set on a
  visible band.
- **DURATION EXCEEDS 300 s CAP** — point is infeasible; suggest crediting (often
  pulls it back under), fewer steps, or dropping the point.
- **UNDER-charged (over-credited)** — assumes the LED held more current than its
  true previous equilibrium → won't fully settle. Reports the implied vs true
  prev current.
- **OVER-charged (under-credited / no credit)** — charging from dark when credit
  was available; wastes current/time.
- **WRONG KNOB** — extended the time while sitting *below* max current; should
  pin at max and shorten.
- **POSSIBLE BANK MIX-UP** — value matches the other bank's from-dark (the
  bank-blind-flux-cal bug).
- **TARGET FLUX IN DEAD ZONE** — flux lands between the low and high ranges for
  that bank and can't be commanded precisely.

## Workflow
1. Identify the sequence CSV and the calibration workbook (ask if the cal isn't
   provided; default to the 260401 workbook, side B).
2. Run the checker. If the cal source/constants are uncertain, say so.
3. Report: how many IR rows pass, the distinct problem types, and the worst
   offenders with file-vs-correct values.
4. Offer `--fix` to produce a corrected CSV; preserve all other columns and the
   sequence's `ACTIVITY_DURATION_SEC` convention (precharge time is usually
   budgeted separately — confirm if durations changed).
5. If auditing a *calculator* (not just a sequence), also check: bank-aware flux
   lookup, root selection in `code_to_flux` (smallest positive real root, with
   an imaginary tolerance — not `real_roots[0]`/exact `np.isreal`), the
   65535/65536 low↔high seam, and whether credit exists at all.
