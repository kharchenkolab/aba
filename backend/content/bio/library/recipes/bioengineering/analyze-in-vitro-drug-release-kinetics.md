---
name: analyze-in-vitro-drug-release-kinetics
description: Fit drug release time-course data to standard kinetic models and identify the best-fitting release mechanism
when_to_use: When characterizing the release profile of a drug from a biomaterial formulation (nanoparticle, hydrogel, implant, etc.)
requires_tools: [run_python]
capabilities_needed: [numpy, pandas, scipy, matplotlib]
keywords: [drug release, kinetics, zero-order, first-order, Higuchi, Korsmeyer-Peppas, biomaterial, formulation, pharmacokinetics]
produces: [drug_release_data.csv, cumulative_release_plot.png, release_rate_plot.png]
domain: bioengineering
source: biomni:tool/bioengineering.py::analyze_in_vitro_drug_release_kinetics
---
# Analyze In Vitro Drug Release Kinetics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept time points (hours) and concentration measurements; convert to numpy arrays.
2. Compute cumulative release percentage relative to `total_drug_loaded` (or max concentration if not supplied).
3. Calculate instantaneous release rate via `np.gradient`.
4. Fit four standard kinetic models using `scipy.optimize.curve_fit`:
   - Zero-order: `Release = k · t`
   - First-order: `Release = 100 · (1 − exp(−k · t))`
   - Higuchi: `Release = k · √t`
   - Korsmeyer-Peppas: `Release = 100 · (k · t)^n` (fit only on data ≤ 60 % release)
5. Compute R² for each model; select the best-fitting model.
6. Estimate t₅₀ (half-life) analytically from the best model's parameters; fall back to linear interpolation when model fitting fails.
7. Interpret the Korsmeyer-Peppas n exponent: n < 0.43 = Fickian diffusion, 0.43–0.85 = anomalous/diffusion-erosion, n > 0.85 = case-II transport.
8. Save cumulative-release plot with all model overlays, a release-rate plot, and a data CSV.

## Key decisions
- Korsmeyer-Peppas fitting is restricted to the first 60 % of release per convention.
- Bounds for first-order: k in [0, 1]; adjust if very fast release kinetics are expected.
- R² is used as the sole model-selection criterion; for production, consider AIC/BIC.

## Caveats
- Concentration data must be monotonically non-decreasing for kinetic model assumptions to hold; validate inputs.
- Very few time points (< 5) give unreliable R² estimates.
- `total_drug_loaded` should be measured experimentally; using max(concentration) as proxy assumes 100 % release was observed.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy", "pandas", "matplotlib")`. Original impl: `source` -> lift to lakeFS later.
