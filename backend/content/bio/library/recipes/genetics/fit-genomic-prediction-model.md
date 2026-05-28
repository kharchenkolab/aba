---
name: fit-genomic-prediction-model
description: Fit a linear mixed model for genomic prediction using genotype and phenotype matrices, estimating variance components and BLUP breeding values with optional dominance effects
when_to_use: When given a genotype matrix and phenotype vector(s) and asked to estimate heritability, breeding values, or genomic prediction accuracy for one or more traits
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, pandas]
keywords: [genomic prediction, GBLUP, BLUP, breeding value, heritability, variance components, genomic relationship matrix, mixed model, additive, dominance]
produces: [results_csv_with_observed_predicted_breeding_values]
domain: genetics
source: biomni:tool/genetics.py::fit_genomic_prediction_model
---
# Fit Genomic Prediction Model

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate that `genotypes` (n_individuals × n_markers) and `phenotypes` (n_individuals [× n_traits]) row counts match.
2. Center genotypes by subtracting column means.
3. Build genomic relationship matrix:
   - Additive: `G = Z @ Z.T / n_markers` where Z is centered genotypes.
   - Additive+Dominance: additionally code heterozygotes (value 1) as 1 and homozygotes as 0 for `G_d = Z_d @ Z_d.T / n_markers`.
4. If `fixed_effects` provided, fit via `np.linalg.lstsq` and subtract fitted values from phenotypes.
5. For each trait run a simplified EM-REML loop (5 iterations):
   - Construct `V = var_g * G + var_e * I`; invert with `scipy.linalg.inv`.
   - Projection matrix `P = V_inv - V_inv @ 1 @ 1.T @ V_inv / (1.T @ V_inv @ 1)`.
   - Update `var_g = y.T P G P y / tr(P G)` and `var_e = y.T P P y / tr(P)`; floor at 0.01.
6. Compute BLUP breeding values: `u = var_g * G @ V_inv @ y_adj`.
7. For additive+dominance also compute dominance deviations; predicted phenotype = `u_a + u_d`.
8. Evaluate accuracy as Pearson correlation between observed and predicted phenotypes.
9. Write per-individual CSV with observed, predicted, and breeding-value columns for each trait.

## Key decisions
- Only 5 EM iterations are used (demonstration quality); production work should use a proper REML solver (e.g. `limix`, `PyGWAS`, or an R bridge to `ASReml`/`sommer`).
- Heritability returned is on the observed scale; transformation to liability scale needed for binary traits.

## Caveats
- Matrix inversion via `scipy.linalg.inv` is O(n³); impractical for large cohorts (>5000 individuals) without sparse or iterative solvers.
- Accuracy metric (training-set correlation) overestimates cross-validated prediction ability.
- Assumes markers are unfiltered and in HWE; pre-filter for MAF and missingness before calling.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
