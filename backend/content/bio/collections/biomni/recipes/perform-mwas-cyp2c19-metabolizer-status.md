---
name: perform-mwas-cyp2c19-metabolizer-status
description: Methylome-wide association study (MWAS) to identify CpG sites associated with CYP2C19 metabolizer status
when_to_use: Epigenome-wide scan for methylation differences between CYP2C19 metabolizer groups (poor/intermediate/normal/rapid/ultrarapid)
requires_tools: [run_python]
capabilities_needed: [pandas, scipy, statsmodels]
keywords: [MWAS, EWAS, CpG, methylation, CYP2C19, pharmacogenomics, metabolizer status, epigenetics, Bonferroni]
produces: [significant CpG sites CSV with coefficients and adjusted p-values]
domain: pharmacology
source: biomni:tool/pharmacology.py::perform_mwas_cyp2c19_metabolizer_status
---
# Perform MWAS for CYP2C19 Metabolizer Status

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load methylation beta-value matrix (samples × CpG sites) and metabolizer status vector from CSV/TSV files; optionally load covariates.
2. Align samples across all inputs; report sample counts.
3. Encode metabolizer status ordinally: poor=1, intermediate=2, normal=3, rapid=4, ultrarapid=5.
4. For each CpG site: if no covariates, use `scipy.stats.linregress`; if covariates present, build an OLS formula (`methylation ~ metabolizer + cov1 + cov2 + ...`) with `statsmodels.formula.api.ols`.
5. Collect slope/coefficient and p-value per site.
6. Apply Bonferroni correction (`p × n_sites`, clipped to 1.0); filter by `adjusted_pvalue < pvalue_threshold`.
7. Save significant sites to CSV; report top 5 hits in the log.

## Key decisions
- Ordinal encoding assumes monotone dose-response across metabolizer categories; not appropriate if the relationship is non-linear.
- Bonferroni is conservative; FDR (Benjamini-Hochberg) is preferred for discovery but not implemented here.

## Caveats
- Runtime scales with the number of CpG sites (450K or 850K arrays); consider chunked execution for large arrays.
- Covariates (age, sex, cell-type composition) are essential to avoid confounding but are optional in this implementation.

## In ABA
Implement with `run_python`; `ensure_capability("pandas", "scipy", "statsmodels")`. Original impl: `source` -> lift to lakeFS later.
