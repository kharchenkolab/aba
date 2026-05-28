---
name: optimize-anaerobic-digestion-process
description: Optimize anaerobic digestion operating conditions to maximize VFA production or methane yield using RSM or genetic algorithm
when_to_use: When given waste characteristics and operational parameter ranges and asked to find optimal HRT, OLR, I/F ratio, temperature, and pH
requires_tools: [run_python]
capabilities_needed: [scipy, numpy, matplotlib]
keywords: [anaerobic digestion, methane yield, VFA, biogas, response surface methodology, genetic algorithm, HRT, OLR, wastewater]
produces: [optimal parameters, predicted yield, response surface plot PNG, sensitivity ranking]
domain: microbiology
source: biomni:tool/microbiology.py::optimize_anaerobic_digestion_process
---
# Optimize Anaerobic Digestion Process

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept waste characteristics (total_solids, volatile_solids, COD) and parameter ranges (HRT, OLR, I/F ratio, temperature, pH).
2. Define empirical objective functions for VFA or methane based on literature-derived polynomial models, scaled by waste characteristics.
3. If method is `rsm`: run `scipy.optimize.minimize` with L-BFGS-B from the midpoint of each range.
4. If method is `genetic`: run `scipy.optimize.differential_evolution` over the same bounds.
5. Record optimal parameter values and predicted yield from the optimizer result.
6. Build a 2D meshgrid over the two most influential parameters (HRT+OLR for VFA; HRT+I/F for methane), compute surface values, and produce a 3D response surface plot with `matplotlib`.
7. Compute normalized sensitivity for each parameter by perturbing ±5% of its range at the optimum.
8. Return a structured log: inputs, convergence stats, optimal conditions, predicted performance, plot path, and sensitivity ranking.

## Key decisions
- VFA model favors short HRT, high OLR, low I/F, mesophilic temp (~35°C), acidic pH (~5.5).
- Methane model favors longer HRT, moderate OLR (~3), high I/F, neutral pH (~7.2).
- Sensitivity is normalized: `|dY/dparam| * (param / Y)` at optimum.

## Caveats
- Objective functions are simplified literature-based models, not mechanistic ADM1; use results as guidance for experimental design, not engineering specification.
- Genetic algorithm is more robust but slower for wide bounds.

## In ABA
Implement with `run_python`; `ensure_capability("scipy", "matplotlib", "numpy")`. Original impl: `source` -> lift to lakeFS later.
