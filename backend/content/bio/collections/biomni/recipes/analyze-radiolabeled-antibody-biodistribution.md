---
name: analyze-radiolabeled-antibody-biodistribution
description: Fit bi-exponential pharmacokinetic models to radiolabeled antibody biodistribution data and compute tumor-to-normal tissue ratios
when_to_use: Pharmacokinetic analysis of radiolabeled biologics from %IA/g tissue measurements over time
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [biodistribution, pharmacokinetics, radiolabeled antibody, tumor targeting, AUC, half-life, bi-exponential]
produces: [PK parameters per tissue, tumor-to-normal ratios, biodistribution JSON results file]
domain: pharmacology
source: biomni:tool/pharmacology.py::analyze_radiolabeled_antibody_biodistribution
---
# Analyze Radiolabeled Antibody Biodistribution

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate that `tumor` is included in `tissue_data`.
2. For each tissue, fit a bi-exponential model `C(t) = A·exp(-α·t) + B·exp(-β·t)` using `scipy.optimize.curve_fit` with bounds `[0,0,0,0]` to `[100,5,100,1]`.
3. Extract pharmacokinetic parameters: distribution half-life `ln2/α`, elimination half-life `ln2/β`, AUC = `A/α + B/β`, mean residence time (MRT), and clearance for blood/plasma.
4. Compute tumor-to-normal tissue ratios at each time point; record maximum ratio and the time at which it occurs.
5. Save all results to a JSON file; return a structured research log with key PK parameters and ratios.

## Key decisions
- Bi-exponential model captures two-phase kinetics typical of IgG antibodies (distribution phase + elimination phase).
- AUC is computed analytically from fitted parameters rather than numerically.
- Clearance is calculated only for blood/plasma tissues.

## Caveats
- Curve fitting may fail for tissues with sparse or noisy time-point data; errors are caught per-tissue and reported.
- Initial parameter guesses `[50, 0.1, 50, 0.01]` suit typical mAb kinetics but may need adjustment for nanobodies or fragments.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy")`. Original impl: `source` -> lift to lakeFS later.
