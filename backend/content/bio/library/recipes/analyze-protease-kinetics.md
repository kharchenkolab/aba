---
name: analyze-protease-kinetics
description: Fit fluorogenic peptide cleavage time-course data to Michaelis-Menten kinetics and extract kcat, KM, and catalytic efficiency
when_to_use: Given fluorescence time-course arrays at multiple substrate concentrations and an enzyme concentration, determine protease kinetic parameters
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, matplotlib]
keywords: [protease, Michaelis-Menten, kcat, KM, catalytic efficiency, fluorogenic, kinetics, enzyme, curve fitting]
produces: [Vmax, KM, kcat, kcat/KM with uncertainties, Michaelis-Menten plot PNG, results TXT]
domain: biochemistry
source: biomni:tool/biochemistry.py::analyze_protease_kinetics
---
# Analyze Protease Kinetics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. For each substrate concentration, fit a linear regression to the first 20% of time points (minimum 5 points) to extract initial velocity.
2. Fit all `(substrate_concentration, initial_velocity)` pairs to the Michaelis-Menten equation `v = Vmax * S / (KM + S)` using `scipy.optimize.curve_fit` with bounds `[0, inf]`.
3. Derive kcat = Vmax / [enzyme]; catalytic efficiency = kcat / KM; propagate uncertainties from the covariance matrix.
4. Plot experimental points and smooth fitted curve with matplotlib; save to `<output_prefix>_mm_plot.png`.
5. Write numerical results (Vmax, KM, kcat, kcat/KM ± SDs) to `<output_prefix>_results.txt`.

## Key decisions
- Initial velocity from linear region of fluorescence curve; using only the early linear portion avoids product inhibition artifacts.
- `curve_fit` initial guesses: p0 = [max(v), mean(S)]; non-negative bounds prevent unphysical fits.
- Standard deviations from `sqrt(diag(covariance))`; reports ± for all four parameters.

## Caveats
- Fluorescence units are arbitrary; kcat/KM units are (a.u./s)/μM unless the user calibrates signal to molar product.
- Assumes single-substrate Michaelis-Menten; does not handle substrate inhibition or cooperative kinetics.
- Linear velocity estimation can fail if only very few early time points are available.

## In ABA
Implement with `run_python`; `ensure_capability("scipy", "matplotlib")`. Original impl: `source` -> lift to lakeFS later.
