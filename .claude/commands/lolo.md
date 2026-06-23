# LOLO APT Run Checklist

Before or after running `populate_lolo_apt.py`:

> **IMPORTANT: LOLO flux scaling reminder**
>
> The LOLO delivered flux is smaller than commanded. The scaling factor depends on the LED:
>
> | LED | Scale factor | Example: target 1000 e⁻/s → commanded |
> |-----|-------------|----------------------------------------|
> | LED14, LED15 (and others) | **6×** | 6000 |
> | LED16 | **14×** | 14000 |

Double-check your `lolo_input_*.csv` before generating the APT file.

---

## Precharge values: use `precharge-calculator`

Never derive precharge fluxes by hand or by scaling. Use the CLI tool:

```
precharge-calculator --side B --target-flux 3000
precharge-calculator --side A --target-flux 120
```

Run it for **each target flux level** in the sweep. It returns `PRECHARGE_FLUX` and `PRECHARGE_TIME` per LED. Use those values directly as the from-dark baseline, then apply the incremental sweep formula below.

---

## Precharge calculation for flux sweeps

LOLO sweeps a single LED upward without turning it off (e.g. 2 → 20 → 200 → 500 e⁻/s). The LED enters each up-step already charged to the previous level's equilibrium, so you only need to supply the **incremental charge**:

```
precharge_current(step N) = full_precharge(target_N) - equilibrium_current(target_{N-1})
```

- **First step (LED cold):** `I_prev = 0` → precharge is identical to from-dark.
- **Each subsequent up-step:** subtract the current the LED already holds at the previous equilibrium → smaller precharge current.

This is the **only** change relative to the from-dark case: credit the previous equilibrium charge so you don't over-drive the LED.
