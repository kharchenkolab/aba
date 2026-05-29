---
name: scvi-de
description: Differential expression with a trained scVI/scANVI model — Bayesian, batch-corrected DE between cell groups via model.differential_expression().
when_to_use: You already have a trained scVI (or scANVI/totalVI) model and want DE between clusters, cell types, or conditions, with batch effects accounted for by the model. Prefer this over scanpy rank_genes_groups when the data has batch structure that scVI already integrated. NOT for bulk RNA-seq (see deseq2-r / bulk-rnaseq-de).
requires_tools: [run_python]
capabilities_needed: [scvi-tools, scanpy, anndata]
keywords: [scVI, scvi-tools, differential expression, DE, differential_expression, bayes factor, proba_de, lfc_mean, change mode, batch correction, marker genes, single cell, one-vs-all]
produces: [scvi_de_results.csv, de_volcano.png]
domain: genomics
source: "scvi-tools 1.3.3 tutorial — Differential expression on C. elegans data (docs.scvi-tools.org/en/1.3.3/tutorials/notebooks/scrna/scVI_DE_worm.html)"
---

# Differential expression with scVI (scvi-tools 1.3.3)

scVI's `differential_expression()` runs a **Bayesian** DE test on the model's
denoised, batch-corrected expression posterior. Instead of a frequentist p-value it
reports a posterior probability of DE (`proba_de`) and a Bayes factor, plus a
log-fold-change posterior. It can correct for batch during the comparison — its main
advantage over scanpy's `rank_genes_groups` when batch structure is present.

**Provision:** `ensure_capability("scvi-tools")` (+ `scanpy`, `anndata`).

**Upstream:** needs a trained model — see **scvi-integration** (scVI) or
**scvi-label-transfer-scanvi** (scANVI). Reload it against the same AnnData.

## Choices to surface with present_plan
- **what to compare** — either a 1-vs-all sweep over a grouping column
  (`groupby="leiden_scVI"`, e.g. cluster markers) or a specific pairwise contrast via
  two boolean masks (`idx1`, `idx2`). idx1 is the "foreground": positive `lfc_mean`
  = up in idx1.
- **`mode="change"`** (recommended) — tests whether the LFC exceeds a biologically
  meaningful threshold `delta` rather than ≠0; report `proba_de` + `is_de_fdr_*`.
- **`batch_correction=True`** — marginalize over batches so the comparison isn't
  confounded by composition; turn on when batches differ between the groups.
- **`weights="importance"`** with `filter_outlier_cells=True` — better FDR
  calibration than the default `"uniform"`; slightly slower.
- **`delta`** — minimum |LFC| to count as DE in change mode (e.g. 0.25–1.0). Larger
  = stricter, fewer hits.
- **hardware** — far cheaper than training; usually fine inline, GPU optional.

## Procedure

```python
import scvi, scanpy as sc, os
DATA = os.environ["DATA_DIR"]

adata = sc.read_h5ad(os.path.join(DATA, "qc_filtered.h5ad"))
model = scvi.model.SCVI.load(os.path.join(DATA, "scvi_model"), adata=adata)

# A) Cluster markers: one-vs-all over a grouping column.
de_markers = model.differential_expression(
    groupby="leiden_scVI",
    mode="change",
    batch_correction=True,
)

# B) A specific pairwise contrast (idx1 = foreground = numerator).
idx1 = (adata.obs["condition"] == "treated").values
idx2 = (adata.obs["condition"] == "control").values
de_pair = model.differential_expression(
    idx1=idx1, idx2=idx2,
    mode="change", delta=0.25,
    weights="importance", filter_outlier_cells=True,
    batch_correction=True,
)

# Rank + filter on the change-mode FDR flag and effect size.
de_pair = de_pair.sort_values("proba_de", ascending=False)
sig = de_pair[(de_pair["is_de_fdr_0.05"]) & (de_pair["lfc_mean"].abs() > 0.5)]
de_pair.to_csv(os.path.join(DATA, "scvi_de_results.csv"))
```

## Reading the output dataframe (per gene)
- `proba_de` — posterior probability the gene is DE (≈1 = strong); `proba_not_de`
  is its complement.
- `lfc_mean` / `lfc_median` / `lfc_std` — log2 fold-change posterior; **positive =
  up in idx1 / the group**. `lfc_std` flags uncertain estimates.
- `bayes_factor` — log Bayesian evidence ratio for DE (large positive = confident).
- `is_de_fdr_0.05` — boolean DE call after FDR control at 0.05 (column name carries
  the `fdr_target`). **Use this for the significance call in change mode.**
- `scale1`, `scale2` — mean normalized expression in each population.

## Caveats to surface
- These are **NOT** frequentist p-values — report `proba_de` / Bayes factor /
  `is_de_fdr_*`, not a "p < 0.05".
- DE quality inherits from integration quality — a badly-integrated model gives
  confounded DE. Sanity-check known markers.
- For very small/imbalanced groups the posterior is wide (`lfc_std` high); be
  cautious.
- This compares the model's denoised posterior, not raw counts; for a pure bulk
  workflow use **deseq2-r** / **bulk-rnaseq-de**.

## Related
- Upstream model: **scvi-integration**, **scvi-label-transfer-scanvi**.
- Protein DE in CITE-seq data: **scvi-totalvi-citeseq**.
