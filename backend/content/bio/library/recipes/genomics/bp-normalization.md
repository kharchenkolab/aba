---
name: bp-normalization
description: Best-practice scRNA-seq normalization — shifted-log (CP10k+log1p), scran pooling size factors, and analytic Pearson residuals, with task-specific guidance per the Single-cell Best Practices book.
when_to_use: Use this for the normalization STAGE only when you need a PRINCIPLED method choice — scran pooling size factors or analytic Pearson residuals (sctransform-style) rather than the default shifted-log — selected by downstream task (DR/DE vs sparse/batch vs rare-cell/feature selection). For a quick end-to-end first pass (which just uses CP10k+log1p) use scrna-qc-clustering; for the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata]
keywords: [scran pooling size factors, deconvolution size factors, analytic Pearson residuals, sctransform, shifted logarithm method choice, normalization method by downstream task]
produces: [adata_normalized.h5ad]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/preprocessing_visualization/normalization.html"
---

# scRNA-seq normalization (best practice)

Normalization removes count-depth (sampling) differences between cells so expression is
comparable. The book stresses there is **no single winner** — pick by downstream task,
and keep the **raw counts** untouched in a layer (count-based methods downstream need them).

**Provision:** `ensure_capability(["scanpy","anndata"])`. Always stash raw counts first:
```python
adata.layers["counts"] = adata.X.copy()
```

## The three methods the book compares

### 1. Shifted logarithm — the workhorse (DR + DE)
CP-median (or CP10k) size-factor scaling, then log1p. Fast, and best at uncovering latent
structure. Use this for **dimensionality reduction and differential expression**.
```python
import scanpy as sc
sc.pp.normalize_total(adata)        # target_sum=None -> median library size (book default)
sc.pp.log1p(adata)
adata.layers["log1p_norm"] = adata.X.copy()
```
Note: `target_sum=1e4` (CP10k) is a common fixed alternative; the fixed value affects
overdispersion estimates, so prefer the median default unless you have a reason.

### 2. scran pooling size factors — for sparse / batch-correction setups
Estimates size factors by pooling cells (deconvolved to per-cell), robust on sparse data.
Needs a quick coarse clustering as input. Runs via R (`scran::computeSumFactors`/`sizeFactors`).
```python
# coarse clusters for pooling
adata_pp = adata.copy(); sc.pp.normalize_total(adata_pp); sc.pp.log1p(adata_pp)
sc.pp.pca(adata_pp); sc.pp.neighbors(adata_pp); sc.tl.leiden(adata_pp, key_added="groups")
# -> pass counts + groups to scran::computeSumFactors via rpy2, then:
# adata.obs["size_factors"] = size_factors
# adata.X = adata.layers["counts"] / size_factors[:,None]; sc.pp.log1p(adata)
```

### 3. Analytic Pearson residuals — for feature selection / rare cells
Residuals of a regularized negative-binomial (akin to sctransform). Removes sampling effects
while preserving heterogeneity; good for **gene selection and rare cell types**. No pseudo-count
/ no log needed.
```python
sc.experimental.pp.normalize_pearson_residuals(adata)   # operates on raw counts
```

## Choosing — quick map
| downstream task | method |
|---|---|
| PCA/UMAP/clustering, DE | shifted log (`normalize_total`+`log1p`) |
| sparse data / integration prep | scran size factors |
| HVG selection, rare-cell detection | analytic Pearson residuals |

## Pitfalls the book calls out
- **Keep raw counts** in `layers["counts"]` — scVI/scANVI, deviance feature selection, and
  pseudobulk DE all need them.
- The **fixed target_sum** (1e4, 1e6) changes overdispersion vs the dataset median — be deliberate.
- No benchmark yet ranks methods by *downstream* impact (Ahlmann-Eltze & Huber 2023 compared 22,
  but on intrinsic metrics) — **validate** for your actual task.
- Normalize on raw counts, not on an already-transformed matrix.

## In ABA
Feeds **`bp-feature-selection`** (HVGs / deviance) then **`bp-dimensionality-reduction`**.
For scVI-based integration, leave counts raw and let the model normalize internally
(see **`bp-data-integration`** / `scvi-integration`).
