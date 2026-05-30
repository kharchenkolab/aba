---
name: bp-quality-control
description: Best-practice scRNA-seq cell QC — MAD-based outlier filtering on counts/genes/mito, ambient-RNA correction (SoupX), and doublet detection (scDblFinder/scrublet) per the Single-cell Best Practices book.
when_to_use: Use this for the rigorous QC STAGE only — MAD-based (data-driven) outlier detection, doublet detection (scrublet/scDblFinder), and ambient-RNA removal (SoupX) — when fixed thresholds are not good enough. For a quick end-to-end first pass use scrna-qc-clustering; for the full rigorous flow start at the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, scrublet]
keywords: [MAD outlier QC, median absolute deviation, data-driven cutoffs, doublets, scrublet, scDblFinder, SoupX, ambient RNA removal, empty droplets, mitochondrial outliers]
produces: [qc_metrics.csv, qc_violins.png, adata_qc.h5ad]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/preprocessing_visualization/quality_control.html"
---

# scRNA-seq quality control (best practice)

QC removes low-quality barcodes (ambient/dying cells, doublets) WITHOUT biasing against
real rare populations. The book's central message: be **lenient and data-driven** (MAD-based),
consider metrics **jointly**, and revisit QC after annotation.

**Provision:** `ensure_capability(["scanpy","anndata","scrublet"])`.

## The three core QC covariates
1. **Count depth** — total counts per barcode.
2. **Number of genes** detected per barcode.
3. **Fraction mitochondrial counts** — high frac flags lysed/dying cells.
Compute them with `sc.pp.calculate_qc_metrics`. Feature-based filtering beyond these three
showed "no benefit downstream" — focus here.

## MAD-based outlier detection (the recommended thresholding)
Don't hand-pick cutoffs. Flag a cell if a metric is > N median-absolute-deviations from the median.
```python
import scanpy as sc, numpy as np
from scipy.stats import median_abs_deviation

adata.var["mt"] = adata.var_names.str.startswith(("MT-", "mt-"))
sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True, percent_top=[20], log1p=True)

def is_outlier(adata, metric, nmads):
    M = adata.obs[metric]
    return (M < np.median(M) - nmads*median_abs_deviation(M)) | \
           (np.median(M) + nmads*median_abs_deviation(M) < M)

# 5 MADs on the (log) library/complexity metrics; stricter 3 MADs on mito
adata.obs["outlier"] = (
    is_outlier(adata, "log1p_total_counts", 5)
    | is_outlier(adata, "log1p_n_genes_by_counts", 5)
    | is_outlier(adata, "pct_counts_in_top_20_genes", 5)
)
adata.obs["mt_outlier"] = is_outlier(adata, "pct_counts_mt", 3) | (adata.obs["pct_counts_mt"] > 8)
adata = adata[~(adata.obs.outlier | adata.obs.mt_outlier)].copy()
sc.pp.filter_genes(adata, min_cells=20)   # drop genes seen in <20 cells
```
Book defaults: **5 MADs** for `log1p_total_counts`, `log1p_n_genes_by_counts`,
`pct_counts_in_top_20_genes`; **3 MADs** for `pct_counts_mt`, plus a hard cap (~8% mito).

## Ambient RNA correction — SoupX (optional, before filtering)
Cell-free mRNA ("soup") contaminates every droplet. **SoupX** (R) estimates the contamination
fraction from the soup profile (empty droplets) and corrects the matrix. Needs BOTH the
filtered and the **raw/unfiltered** CellRanger matrix; coarse clustering improves it.
Run via R: `SoupChannel()` -> `setClusters()` -> `autoEstCont()` -> `adjustCounts()`.

## Doublet detection (flag, don't auto-delete)
The book recommends **scDblFinder** (R, top performer); **scrublet** is the scanpy-native option.
Run it **per sample** — never on batch-aggregated data.
```python
sc.pp.scrublet(adata)   # adds obs['predicted_doublet'], obs['doublet_score']; run per-sample
```
The book advises KEEPING flagged doublets initially and inspecting them during visualization,
removing only after you see where they sit.

## Outputs
- `qc_metrics.csv` — per-cell metrics before/after filtering.
- `qc_violins.png` — `sc.pl.violin(adata, ["total_counts","n_genes_by_counts","pct_counts_mt"])`.
- `adata_qc.h5ad` — filtered object (doublet flags retained).

## Pitfalls the book calls out
- **Don't over-filter** — strict cutoffs erase small/rare subpopulations.
- **Joint, not independent** thresholds — high mito + low complexity together means dying; high mito
  alone can be a real high-respiration cell type.
- **Per-sample doublet calls** — aggregating batches breaks the detector.
- **Reassess after annotation** — once cell types are known, some "outliers" are real biology.

## In ABA
After QC, normalize with **`bp-normalization`**. For a fast, opinionated QC+clustering combo
(fixed thresholds, no MAD), use **`scrna-qc-clustering`** instead.
