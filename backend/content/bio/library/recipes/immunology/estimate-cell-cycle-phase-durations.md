---
name: estimate-cell-cycle-phase-durations
description: Estimate G1/S/G2M durations and death rate from dual-nucleoside (EdU/BrdU) pulse-labeling flow cytometry data via parameter optimization
when_to_use: When dual-pulse labeling time-course data (EdU+, BrdU+, double-positive percentages) are available and cell cycle phase durations need to be inferred
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [cell cycle, EdU, BrdU, pulse labeling, G1, S phase, G2M, proliferation, flow cytometry, parameter fitting]
produces: [optimized phase durations, death rate, optimization report log]
domain: immunology
source: biomni:tool/immunology.py::estimate_cell_cycle_phase_durations
---
# Estimate Cell Cycle Phase Durations

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `flow_cytometry_data` dict with keys `time_points`, `edu_positive`, `brdu_positive`, `double_positive` (all as lists).
2. Accept `initial_estimates` dict with `g1_duration`, `s_duration`, `g2m_duration`, `death_rate`.
3. Define a simplified ODE-based cell population simulator: for each time point compute expected EdU+, BrdU+, double+ fractions from current G1/S/G2M and death rate.
   - S-phase fraction = `s / (g1 + s + g2m)`
   - EdU+ ≈ `s_frac * exp(-death * t)` (scaled to %)
   - BrdU+ ≈ `s_frac * (1 - exp(-t / s))` (scaled to %)
   - Double+ ≈ product of both
4. Define an objective: sum of squared errors across all three observables and all time points.
5. Minimize with `scipy.optimize.minimize` using method `L-BFGS-B` and bounds `[(0.1,50),(0.1,30),(0.1,20),(0,1)]`.
6. Report optimized values, percent change vs. initial estimates, total cycle time, and convergence status.

## Key decisions
- L-BFGS-B chosen for bounded optimization without gradient computation.
- Simplified analytical model (not full ODE solver) keeps fitting fast; replace with full ODE for publication-quality results.
- Death rate bounded to [0, 1] per hour to prevent degenerate solutions.

## Caveats
- The built-in simulator is a first-order approximation; real cell cycle dynamics require solving a full age-structured PDE or ODE system.
- Results are sensitive to initial estimates; provide biologically plausible starting values.
- Optimization may converge to a local minimum; consider multi-start with varied initials.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy")`. Original impl: `source` -> lift to lakeFS later.
