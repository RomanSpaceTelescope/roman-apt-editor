# RCS Precharge — Reference

Background and the exact rules the checker enforces. Companion to `SKILL.md`.

## 1. Why precharge
IR LEDs (bands 4–6) settle slowly (~60 s cold). Precharge drives the LED briefly
*above* the science current to reach equilibrium faster, then drops to the
science level — cutting settle to a ~30 s target. Visible bands (1–3) settle
fast and are not precharged.

## 2. Charge model
The charge needed to settle scales with the **step** in drive current:

```
Q = pc_band · ΔI          ΔI = I_target − I_prev      [mA·s]
Q = I_pc · t_pc           (delivered as current × time)
```

`pc_band` is an empirical per-band constant. There are **two knobs** (current,
time) and one equation:

- **Current headroom** → fix `t = 30 s`, set `I_pc = pc·ΔI / 30`. (Credit lowers current.)
- **At the ceiling** (`I_pc` would exceed `I_max ≈ 96 mA`) → pin `I_pc = I_max`,
  set `t = pc·ΔI / I_max`. (Credit lowers time.) Time saved by credit `= pc·I_prev / I_max`.

**Credit-spending priority when clamped.** As credit grows (`Q` shrinks), spend
it on **time first**: keep current at max and let the extended duration fall
**down to the 30 s floor**. Only after `t` reaches 30 s does further credit go
to **current** (duration held at 30 s, current drops below max). Net: duration
is bounded to `[30 s, 300 s]`, current to `(0, I_max]`, and the regime crossover
is exactly where the from-max time would hit 30 s.

Hard cap: `t ≤ 300 s`. A point needing more is invalid for APT — credit it,
split it, use fewer steps, or drop it. (Credit often pulls a clamped point back
under 300 s.)

## 3. Step-aware credit
Within a sweep the LED stays lit, so each up-step only needs the *incremental*
charge from the previous equilibrium:

- **First step of a sweep:** from dark (`I_prev = 0`).
- **Each subsequent up-step:** `I_prev` = the previous step's science current.
- A sweep = consecutive rows sharing `(PASSPLAN_LABEL, OBSERVATION_NUMBER, LED)`,
  ordered by ascending flux. `CLEANUP=YES` ends it.

Credit reduces `ΔI`, hence `Q`. Whether that shows up as lower current or shorter
time depends only on whether you're against the current ceiling — it is the same
credit. **Never** reduce the precharge flux *and* keep the long from-dark
duration (that under-charges and is slower than needed).

## 4. The 10-minute decay rule
The charge state is not a latch — it decays once the LED is off. Empirically,
~**10 minutes** off returns the LED to (effectively) cold. So:

- After `CLEANUP=YES` (or any long off-gap), the LED's next use starts from dark.
- `CLEANUP=YES` is a *heuristic flag* for "enough off-time elapses before reuse."
- If a sequence re-fires the same LED **within** ~10 min of turning it off, it
  still holds partial charge and the from-dark assumption is conservative — it
  could be credited further. The checker emits a note when it detects this
  (using `CUMULATIVE_TIME_SEC`).

## 5. Calibration & constants
- **Side / box:** Side A = box 1, Side B = box 2.
- **Flux→code** is a piecewise cubic: low range (`code ≤ 65535`) and high range,
  with a possible dead zone between `low_max` and `high_min`. **Look it up by
  (band, bank).** The 4/01 high-range cubic terms are ≈0, so the high inverse is
  effectively linear, but solve it robustly anyway (smallest positive real root,
  imaginary tolerance ~1e-6).
- **Current curves** (`LED Calibration` sheet) are per (box, bank): linear
  `code = m·I + b` with separate high/low segments.
- **Precharge constants** [mA·s/mA] — validated STV/TV2 values:
  - Side B: `{4: 2164, 5: 1300, 6: 1040}`
  - Side A: `{4: 2164, 5: 1633, 6: 1040}`
  - The workbook `Precharge` sheet holds a **stale 2024-03-19 baseline**
    `{1665,1000,800}` (~23 % low, identical for both sides) — do **not** use it.
- **Max current** ≈ 95.9 mA (flux = `high_max − 2`); code cap `2·65535 − 1 = 131069`.

## 6. Known calculator bugs (audit checklist)
1. **Bank-blind flux cal** — building the flux dict keyed by band only (so the
   bank-2 row overwrites bank-1), or a getter that ignores its `bank` argument.
   Result: every LED uses bank-2 flux→code; bank-1 results off by ~−10 %…+4 %
   (worst where a bank-1-valid flux lands in bank-2's dead zone → snaps to
   `0xffff`). Tell-tales: stored `high_min` equals the bank-2 value; `LEDb1` and
   `LEDb2` give near-identical precharge.
2. **No credit** — a single-`target_flux` calculator only does from-dark. Credit
   then gets layered on elsewhere and is where inconsistencies creep in
   (over-credited mid-steps, wrong knob at the ceiling).
3. **Fragile root pick** — `code_to_flux` using `real_roots[0]` and exact
   `np.isreal` can pick a non-physical root or drop a real root with a tiny
   imaginary residue. Use `abs(imag) < tol` and the smallest positive in-range root.
4. **Low↔high seam** — `current_to_hex` thresholding at `hex_to_current(65535)`
   while `hex_to_current` switches at `code > 65536`: a 1-code inconsistency.
5. **Wrong knob at the ceiling** — extending duration while driving below max
   current (should pin at max).
6. **f-string with a backslash** in an expression (`f"...{d['k\n']}..."`) — only
   parses on Python ≥ 3.12.

## 7. Sequence-level sanity
- Visible bands (1–3) precharge columns blank.
- IR bands (4–6) have both precharge flux and duration.
- No duration > 300 s.
- `ACTIVITY_DURATION_SEC` convention: in the LOLO files it does **not** include
  precharge time (precharge budgeted separately). If a fix changes durations,
  confirm the timeline accounting still closes.
- Flux scaling (e.g. a LOLO-filter factor) is an input decision — confirm the
  intended multiplier with the author; the checker validates precharge *for the
  given target fluxes*, not the flux choice.
