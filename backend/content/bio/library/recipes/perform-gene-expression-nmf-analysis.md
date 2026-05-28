---
name: perform-gene-expression-nmf-analysis
description: Decompose a gene-expression matrix via NMF to extract metagenes and sample weights for tumor subtype discovery
when_to_use: Given a genes × samples expression matrix (bulk or pseudo-bulk), identify latent gene programs (metagenes) and assign each sample a weight per program — useful for subtype discovery, immune deconvolution, or pathway activity scoring
requires_tools: [run_python]
capabilities_needed: [numpy, pandas, scikit-learn]
keywords: [nmf, non-negative matrix factorization, metagenes, tumor subtypes, latent factors, decomposition, gene programs, cancer]
produces: [metagenes.csv (genes × components), sample_weights.csv (components × samples), top_genes_per_metagene.csv (top 20 genes per metagene)]
domain: cancer_biology
source: biomni:tool/cancer_biology.py::perform_gene_expression_nmf_analysis
---
# Gene Expression NMF Analysis

Distilled from a biomni implementation. In ABA, implement with the libraries
below — not biomni.

## Approach
1. Load CSV or TSV expression file with `pd.read_csv(..., index_col=0)`; rows = genes, columns = samples.
2. Check for negative values; if found, take absolute values (warn user).
3. If `normalize=True`, apply column-wise TPM-like scaling: `X = X / X.sum(axis=0) * 1000`.
4. Fit `sklearn.decomposition.NMF(n_components=n, init='random', random_state=42, max_iter=1000)`.
5. Extract W (genes × components) via `fit_transform(X)` and H (components × samples) via `model.components_`.
6. Save W as `metagenes.csv`, H as `sample_weights.csv`.
7. For each metagene, rank genes by W column descending, take top 20; save as `top_genes_per_metagene.csv`.
8. Report reconstruction error (`model.reconstruction_err_`) and iteration count.

## Key decisions
- Default `n_components=10`; this is a hyperparameter — consider running stability analysis (e.g. cophenetic correlation across ranks) to select n.
- `init='random'` with fixed `random_state=42` for reproducibility; `init='nndsvd'` is often better for sparse data.
- Normalization scales each sample column to sum to 1000 (TPM-like), NOT log-normalization; adjust if input is already log-transformed.
- Top-20 genes per metagene are ranked by raw W weight, not by specificity score.

## Caveats
- NMF requires non-negative input; log-transformed data (which has negative values) must be handled before calling.
- Reconstruction error alone is insufficient to choose `n_components`; use cophenetic correlation or silhouette scores.
- Large matrices (>20k genes) may be slow with `max_iter=1000`; consider feature selection first.
- Results are sensitive to initialization; run multiple random seeds and select the most stable solution.

## In ABA
Implement with `numpy`, `pandas`, `scikit-learn`; `ensure_capability("scikit-learn")`. Original impl: `biomni:tool/cancer_biology.py::perform_gene_expression_nmf_analysis` → lift to lakeFS later.
