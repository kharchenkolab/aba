---
name: bp-dimensionality-reduction
description: Best-practice scRNA-seq dimensionality reduction — PCA on HVGs as the analysis representation, with t-SNE/UMAP only for visualization, per the Single-cell Best Practices book.
when_to_use: Use this for the dimensionality-reduction STAGE only when you want the principled distinction enforced — PCA as the COMPUTE representation (choosing n_pcs from the variance elbow, PCA diagnostics) vs UMAP/t-SNE/diffusion map as view-only embeddings you must NOT compute on. For a quick end-to-end first pass (which just runs PCA+UMAP with defaults) use scrna-qc-clustering; for the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata]
keywords: [PCA diagnostics, variance ratio elbow, number of PCs, compute representation vs visualization, diffusion map, t-SNE PCA init, axes have no meaning]
produces: [pca_variance.png, umap.png, adata_dimred.h5ad]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/preprocessing_visualization/dimensionality_reduction.html"
---

# scRNA-seq dimensionality reduction (best practice)

Two distinct purposes, do not conflate them: **PCA** produces the representation you COMPUTE on
(neighbors, clustering, integration); **UMAP/t-SNE** produce a 2D picture you only LOOK at.

**Provision:** `ensure_capability(["scanpy","anndata"])`. Run on log-normalized data with HVGs
flagged (`bp-feature-selection`).

## PCA — the analysis representation
Linear, interpretable, fast. Restrict to HVGs; use **10-50 PCs** downstream (not for viewing).
```python
import scanpy as sc
sc.pp.pca(adata, n_comps=50, svd_solver="arpack", mask_var="highly_variable")
sc.pl.pca_variance_ratio(adata, n_pcs=50, log=True, save="_variance.png")  # eyeball the elbow
```
Pick the number of PCs from the variance-ratio elbow (often ~30-50). PCA itself is a poor
*visualization* for sparse scRNA-seq (dropout + nonlinearity), so don't read biology off a PCA scatter.

## Neighbor graph -> UMAP (visualization)
```python
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)   # graph on the PCA space
sc.tl.umap(adata)
sc.pl.umap(adata, color=["total_counts","pct_counts_mt","predicted_doublet"], save=".png")
```
UMAP preserves local + some global structure. Overlay QC metrics + doublet scores here to spot
low-quality regions you may want to revisit (`bp-quality-control`).

## t-SNE (alternative view)
Emphasizes local structure; initialize from PCA for stability.
```python
sc.tl.tsne(adata, use_rep="X_pca")
```

## Pitfalls the book calls out
- **"The axes have no meaning"** — never interpret UMAP/t-SNE axis values or read absolute
  inter-cluster distances off the 2D embedding. Use the graph/PCA space for quantitative claims.
- Compute neighbors/clustering on **PCA**, not on UMAP coordinates.
- Normalize + select HVGs first; PCA on raw counts is dominated by depth.
- t-SNE is sensitive to perplexity and init -> PCA init.
- For very large data, `rapids-singlecell` accelerates PCA/neighbors/UMAP 1-2 orders of magnitude.

## In ABA
The PCA space (or an integrated embedding from **`bp-data-integration`** / `scvi-integration`)
feeds **`bp-clustering`** via `sc.pp.neighbors(..., use_rep=...)`. Diffusion maps (`sc.tl.diffmap`)
for trajectory work live in **`bp-trajectory-inference`**.
