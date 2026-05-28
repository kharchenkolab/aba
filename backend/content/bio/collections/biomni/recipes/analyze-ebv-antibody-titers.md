---
name: analyze-ebv-antibody-titers
description: Quantify EBV antibody titers (VCA-IgG/M, EA-IgG/M, EBNA1-IgG/M) from ELISA OD data using standard curve interpolation
when_to_use: When given raw OD readings from an EBV ELISA panel, standard curve concentration-OD pairs, and sample metadata, and needing antibody concentrations with group summaries
requires_tools: [run_python]
capabilities_needed: [numpy, pandas]
keywords: [EBV, Epstein-Barr, ELISA, antibody titers, VCA, EBNA1, EA, IgG, IgM, serology, standard curve]
produces: [ebv_antibody_titers_results.csv, per-group mean and SD summary log]
domain: immunology
source: biomni:tool/immunology.py::analyze_ebv_antibody_titers
---
# Analyze EBV Antibody Titers

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `raw_od_data` dict `{sample_id: {antibody_type: OD_value}}`, `standard_curve_data` dict `{antibody_type: [(concentration, OD), ...]}`, and `sample_metadata` dict `{sample_id: {group, collection_date}}`.
2. Check for missing antibody readings across the six targets (VCA_IgG, VCA_IgM, EA_IgG, EA_IgM, EBNA1_IgG, EBNA1_IgM); log warnings.
3. For each antibody type in standard curve data, fit a linear regression with `numpy.polyfit(ODs, concentrations, 1)` to get slope and intercept.
4. For each sample, look up the IgG or IgM curve by suffix; compute `concentration = slope * OD + intercept`.
5. Assemble results into a pandas DataFrame with sample ID, group, collection date, and all six concentrations.
6. Group by `Group`; compute mean ± SD for each antibody type.
7. Save full results to `ebv_antibody_titers_results.csv`.

## Key decisions
- Separate IgG and IgM standard curves to handle different assay calibrations.
- Linear regression (OD → concentration) rather than 4PL sigmoid; suitable for OD values within the linear range of the standard curve.
- `numpy.polyfit` with degree 1 used for simplicity; upgrade to 4-parameter logistic if the standard curve is non-linear.

## Caveats
- Linear interpolation is only valid within the OD range of the standard curve; extrapolated values should be flagged.
- No duplicate-well averaging or CV filtering is implemented; caller should pre-average technical replicates.
- Seropositivity cut-offs (e.g. VCA-IgG > 20 U/mL) are not applied here; add clinical interpretation thresholds separately.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
