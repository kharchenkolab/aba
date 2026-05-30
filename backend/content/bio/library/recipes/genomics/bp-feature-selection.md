---
name: bp-feature-selection
description: Best-practice scRNA-seq feature selection — deviance-based selection on raw counts (scry) and scanpy highly_variable_genes, avoiding normalization-induced artifacts, per the Single-cell Best Practices book.
when_to_use: Use this for the feature-selection STAGE only when you want a PRINCIPLED gene-selection method — deviance-based selection on raw counts (scry) to dodge normalization sensitivity, or a deliberate HVG flavor choice. For a quick end-to-end first pass (which just calls highly_variable_genes with defaults) use scrna-qc-clustering; for the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, scry]
keywords: [deviance feature selection, binomial deviance, scry devianceFeatureSelection, analytic Pearson residuals feature selection, seurat_v3 vs seurat flavor, normalization-robust gene selection]
produces: [adata_hvg.h5ad, hvg_list.csv]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/preprocessing_visualization/feature_selection.html"
---

# scRNA-seq feature selection (best practice)

Selecting informative (highly variable) genes reduces noise/dimensionality before PCA.
The book's headline: classic dispersion/CV-based HVG selection is **sensitive to the
normalization + pseudo-count** used; **deviance on raw counts** sidesteps that entirely.

**Provision:** `ensure_capability(["scanpy","anndata","scry"])`. Requires raw counts retained
(`adata.layers["counts"]` from `bp-normalization`).

## Recommended: deviance-based selection (raw counts, scry)
Ranks genes by binomial **deviance** under a multinomial null — closed-form, no normalization,
no pseudo-count bias. The book selects the top ~**4000** deviant genes.
```python
# scry::devianceFeatureSelection (R) on the RAW counts matrix, via rpy2
# returns a per-gene binomial deviance; take the top N
import scanpy as sc
# binomial_deviance = scry.devianceFeatureSelection(SingleCellExperiment(counts))  # via rpy2
idx = binomial_deviance.argsort()[::-1][:4000]
adata.var["highly_deviant"] = False
adata.var.iloc[idx, adata.var.columns.get_loc("highly_deviant")] = True
adata.var["highly_variable"] = adata.var["highly_deviant"]   # so PCA's mask_var picks it up
```

## Standard alternative: scanpy highly_variable_genes
Fast and built-in. Two solid flavors:
```python
# (a) seurat_v3 expects RAW counts and models mean-variance directly:
sc.pp.highly_variable_genes(adata, flavor="seurat_v3", n_top_genes=2000, layer="counts")
# (b) classic seurat/cell_ranger flavor on LOG-normalized data:
sc.pp.highly_variable_genes(adata, flavor="seurat", n_top_genes=2000)   # on log1p data
```
For integration, pass `batch_key=` so HVGs are chosen consistently across batches
(see `bp-data-integration`).

## Pearson-residual route
If you normalized with analytic Pearson residuals, select on those residuals' variance
(`flavor="pearson_residuals"`) — coherent with that normalization and good for rare cells.

## How many genes
Traditional 500-2000; the book uses **~4000** with deviance. More genes = more rare-cell signal
but more noise; fewer = cleaner but risks dropping rare markers. 1000-2000 is a safe default for
the seurat flavors.

## Pitfalls the book calls out
- **CV/dispersion HVGs are normalization-sensitive**; a near-zero pseudo-count inflates variance.
  Deviance on raw counts avoids this.
- Match the flavor to the data layer: `seurat_v3` wants **raw counts**, classic `seurat` wants
  **log-normalized**. Mismatching silently degrades selection.
- Don't subset the object to HVGs destructively if you'll later annotate or do DE — keep all genes
  and mask via `highly_variable` (PCA/`mask_var` reads the flag).

## In ABA
Feeds **`bp-dimensionality-reduction`** (PCA restricted to HVGs). For integration, choose HVGs
with `batch_key=` before **`bp-data-integration`**.
