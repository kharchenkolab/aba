---
name: analyze-bacterial-growth-curve
description: Fit a logistic growth model to bacterial OD time-series and extract key growth parameters
when_to_use: When given OD600 (or similar) measurements over time for a bacterial culture and needing doubling time, growth rate, lag phase, or carrying capacity
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, pandas, matplotlib]
keywords: [bacterial growth, OD600, doubling time, growth rate, lag phase, carrying capacity, logistic model, microbiology]
produces: [growth parameters JSON, growth curve PNG, analysis log]
domain: immunology
source: biomni:tool/immunology.py::analyze_bacterial_growth_curve
---
# Analyze Bacterial Growth Curve

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `time_points` (hours) and `od_values` arrays plus a `strain_name` string.
2. Convert inputs to `numpy` arrays; build a pandas DataFrame for inspection.
3. Define the logistic growth model: `N(t) = K / (1 + ((K - N0) / N0) * exp(-r*t))`.
4. Set initial parameter guesses: `p0 = [max(OD), OD[0], 0.5]`.
5. Use `scipy.optimize.curve_fit` to fit K (carrying capacity), N0 (initial OD), and r (growth rate).
6. Derive doubling time: `ln(2) / r`.
7. Estimate lag phase: `(ln((K/N0) - 1) - ln((K/(0.05*K)) - 1)) / r`, clipped to ≥ 0.
8. Plot observed scatter + fitted curve with matplotlib; save PNG to `output_dir`.
9. Return a structured text log with all parameters and file paths.

## Key decisions
- Logistic model chosen over exponential to capture stationary phase.
- Lag phase estimated analytically from fitted parameters (time to reach 5 % of K).
- `RuntimeError` from `curve_fit` (convergence failure) is caught; return descriptive error message.

## Caveats
- Model assumes a single growth phase; biphasic or diauxic growth will fit poorly.
- Initial guess `p0[2] = 0.5` may fail for very slow-growing strains; caller may need to tune.
- OD readings above ~0.8 are non-linear with cell density; advise dilution if needed.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy", "pandas", "matplotlib")`. Original impl: `source` -> lift to lakeFS later.
