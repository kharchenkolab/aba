---
name: perform-cosinor-analysis
description: Fit a cosinor model to physiological time-series data to extract circadian rhythm parameters
when_to_use: When a user has repeated physiological measurements over time and wants to characterize circadian (or other periodic) rhythms
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [cosinor, circadian, rhythm, mesor, amplitude, acrophase, periodicity, chronobiology]
produces: [mesor, amplitude, acrophase in radians and hours, R-squared, standard errors, research log]
domain: physiology
source: biomni:tool/physiology.py::perform_cosinor_analysis
---
# Perform Cosinor Analysis

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Define the cosinor model: `y = mesor + amplitude * cos(2π*t/period - acrophase)`.
2. Set initial guesses: mesor = mean(data), amplitude = (max-min)/2, acrophase = 0.
3. Fit with `scipy.optimize.curve_fit`; extract mesor, amplitude, acrophase and their standard errors from the covariance matrix diagonal.
4. Convert acrophase from radians to clock hours: `hours = (acrophase * period) / (2π) mod period`.
5. Calculate R² from residual and total sum of squares.
6. Flag weak rhythmicity if R² < 0.3.

## Key decisions
- Period defaults to 24 h for circadian analysis but is a free parameter — works for ultradian or infradian rhythms.
- `curve_fit` uses nonlinear least squares; well-suited for unequally spaced time points.
- Standard errors give confidence on each parameter without requiring bootstrapping.

## Caveats
- Assumes a single harmonic component; multi-component rhythms (e.g., ultradian superimposed on circadian) require additional harmonics.
- Requires at least one full cycle of data; sparse or short time series yield unreliable fits.
- `curve_fit` may fail if initial guesses are poor — report the exception with parameter suggestions.

## In ABA
Implement with `run_python`; `ensure_capability(["numpy", "scipy"])`. Original impl: `source` -> lift to lakeFS later.
