---
name: quantify-biofilm-biomass-crystal-violet
description: Quantify biofilm biomass from crystal violet OD readings by control subtraction, statistics, and significance testing
when_to_use: When given OD595 values from a crystal violet biofilm assay and asked to quantify biomass or compare samples to control
requires_tools: [run_python]
capabilities_needed: [numpy, pandas, scipy]
keywords: [biofilm, crystal violet, OD595, biomass quantification, biofilm assay, microtiter plate, statistical significance]
produces: [normalized biomass values, t-test p-values, results CSV, research log]
domain: microbiology
source: biomni:tool/microbiology.py::quantify_biofilm_biomass_crystal_violet
---
# Quantify Biofilm Biomass by Crystal Violet Staining

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept OD values array and optional sample names; default sample names are "Sample 1", "Sample 2", etc.
2. Subtract the control (specified by index) from all values to obtain normalized biomass.
3. Compute mean and standard deviation of positive (> 0) normalized values.
4. For each non-control sample, run a one-sample t-test (`scipy.stats.ttest_1samp`) against zero to assess significance vs. control.
5. Assemble results DataFrame (Sample, OD_Value, Normalized_Value) and save to CSV if a save path is provided.
6. Return a research log covering sample ODs, normalization, statistics, p-values, and interpretation.

## Key decisions
- Control subtraction is performed before any statistical test.
- One-sample t-test against 0 is used; with replicates a two-sample test would be more appropriate — pass replicate arrays when available.
- Significance threshold is p < 0.05.

## Caveats
- One-sample t-test on a single value yields no meaningful p-value; provide technical replicates (≥3) per sample for valid inference.
- Crystal violet measures total biomass including dead cells; does not distinguish live from dead biofilm.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "pandas", "scipy")`. Original impl: `source` -> lift to lakeFS later.
