---
name: analyze-enzyme-kinetics-assay
description: Simulate and analyze in vitro enzyme kinetics including time-course, Michaelis-Menten fitting, and dose-response IC50 for modulators
when_to_use: Given enzyme name, substrate concentrations, enzyme concentration, and optional modulator concentration series, characterize enzyme kinetics and inhibition
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, pandas]
keywords: [enzyme kinetics, Michaelis-Menten, IC50, Hill coefficient, dose-response, inhibitor, modulator, Vmax, Km]
produces: [time-course CSV, substrate kinetics CSV, dose-response CSV per modulator, Vmax/Km, IC50/Hill coefficient]
domain: biochemistry
source: biomni:tool/biochemistry.py::analyze_enzyme_kinetics_assay
---
# Analyze Enzyme Kinetics Assay

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Generate (or accept) time-course activity data; identify the linear range as the first ~30% of the saturation curve.
2. Simulate substrate-dependent activity using Michaelis-Menten kinetics with Gaussian noise; fit with `scipy.optimize.curve_fit` to recover Vmax and Km.
3. For each modulator: simulate a sigmoidal dose-response (100 / (1 + (c/IC50)^Hill)) and fit with `curve_fit` using bounds `[0.1, 1000]` for IC50 and `[0.1, 10]` for Hill coefficient; require ≥3 non-zero concentration points.
4. Save time-course, kinetics, and per-modulator dose-response to separate CSV files.

## Key decisions
- Biomni uses `np.random.seed(42)` for reproducible simulated data; real assays will supply measured values directly.
- Modulator IC50 fit excludes the zero-concentration point to avoid log-scale issues.
- At least 4 total points (3 non-zero) required before attempting IC50 fit.

## Caveats
- The implementation simulates data when real measurements are absent; for actual assays, replace simulation with measured fluorescence/absorbance arrays.
- Modulator classification (competitive/noncompetitive) is not determined; only phenomenological IC50 is reported.
- Noise level and kinetic constants are hard-coded defaults suitable only for demonstration.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
