---
name: analyze-bacterial-growth-rate
description: Fit a Gompertz growth model to OD600 time-course data and extract lag time, maximum growth rate, doubling time, and carrying capacity.
when_to_use: When a user provides bacterial OD600 measurements over time and wants quantitative growth parameters and a fitted growth curve plot.
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, matplotlib]
keywords: [bacterial growth, OD600, Gompertz model, growth rate, doubling time, lag phase, carrying capacity, microbiology]
produces: [fitted growth parameters, PNG growth curve plot, research log]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::analyze_bacterial_growth_rate
---
# Analyze Bacterial Growth Rate

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Convert `time_points` and `od_measurements` to numpy arrays.
2. Define the Gompertz model: `A * exp(-exp(mu_max * e * (lag - t) / A + 1))`.
3. Set initial guesses: lag = mean(t)/3, mu_max = 0.5, A = 1.1 × max(OD).
4. Call `scipy.optimize.curve_fit` with non-negative bounds to fit lag, mu_max, and carrying capacity.
5. Derive doubling time as `ln(2) / mu_max`.
6. Generate a smooth fitted curve and plot with `matplotlib`; save PNG to `output_dir`.
7. Return a markdown log with all parameters and the plot filename.
8. On `RuntimeError` (fit failure), return a diagnostic log with suggestions.

## Key decisions
- Gompertz model captures lag, exponential, and stationary phases in a single three-parameter form.
- Non-negative parameter bounds prevent physiologically nonsensical solutions.
- Matplotlib uses the `Agg` (non-interactive) backend for headless execution.

## Caveats
- Fit may diverge with sparse data (<8 points) or atypical growth patterns; check the RuntimeError branch.
- Doubling time is only meaningful in the exponential phase; the Gompertz `mu_max` is a global fit.

## In ABA
Implement with `run_python`; `ensure_capability("scipy")`, `ensure_capability("matplotlib")`. Original impl: `source` -> lift to lakeFS later.
