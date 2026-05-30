---
name: bulk-rnaseq-de
description: Bulk RNA-seq differential expression with pydeseq2 (Python) — designs, covariate control, and contrasts. Wald-only (no LRT).
when_to_use: Bulk RNA-seq RAW counts + sample table; want DE between conditions in a PYTHON session (scanpy/anndata already in play) or when the user wants Python / no R. If the question needs the likelihood-ratio test, or the session is R-based, or the user asks for DESeq2/R, use the deseq2-r recipe instead. SCOPE — bulk or pseudobulk-aggregated counts ONLY; NOT for direct per-cell scRNA-seq DE.
avoid_when: "Direct per-cell scRNA-seq DE (use rank_genes_groups/Wilcoxon or scVI); NO biological replicates — two clusters within a SINGLE sample pseudobulk to n=1 per group and the DE statistics are then invalid; only a gene list (use enrichment); you need LRT / multi-factor / arbitrary contrasts (use deseq2-r — pydeseq2 is Wald-only)."
requires_tools: [run_python]
capabilities_needed: [pydeseq2, adjusttext]
keywords: [pydeseq2, DESeq2, bulk RNA-seq, differential expression, contrast, covariate, batch, log2 fold change, volcano, Python]
produces: [de_results.csv, volcano.png, ma_plot.png, top_hits.csv]
resource_profile: small (~10s for a typical bulk study)
domain: genomics
source: "pydeseq2 0.5.x docs (pydeseq2.readthedocs.io) — Owkin's Python port of DESeq2"
---

# Bulk RNA-seq DE with pydeseq2 (Python)

pydeseq2 is a Python re-implementation of DESeq2 — convenient in a Python-native
session, no R needed. **It is Wald-only — there is no LRT.** If the question
needs the likelihood-ratio test, or the session is already in R, or the user
asks for DESeq2/R, use the **`deseq2-r`** recipe (authoritative, fuller feature set).

> **Bulk / pseudobulk only.** DESeq2-family methods model gene-level **negative-binomial counts
> per sample**. For single-cell data they apply ONLY to **pseudobulk** (sum raw counts per
> sample × cell type — see **`bp-differential-expression`**), never to a per-cell count matrix.
> For **direct per-cell scRNA-seq DE** use `sc.tl.rank_genes_groups` (Wilcoxon — cluster/marker
> genes; see **`scrna-qc-clustering`** / **`bp-annotation`**) or scVI's model-based DE
> (**`scvi-de`**). Per-cell DESeq2 commits pseudoreplication and inflates the FDR.
> Pseudobulk also needs **≥2 biological REPLICATES per group** (multiple samples/donors per
> condition). Comparing two **clusters within a SINGLE sample** pseudobulks to n=1 per group —
> DESeq2 will run but its statistics are MEANINGLESS (no dispersion estimate). For cluster-vs-
> cluster DE in one sample, use `rank_genes_groups` (Wilcoxon); reach for pseudobulk-DESeq2 only
> when you have a real replicated condition contrast (e.g. disease vs control across donors).

**Provision:** `ensure_capability("pydeseq2")` (and `adjusttext` for volcano labels).

## The two choices that DEFINE the analysis — surface them with present_plan
1. **Design / model** — variable of interest **LAST**; earlier terms are controlled for.
2. **Contrast** — which levels, which direction (flips the log2FC sign).
This is where an advisor should walk the user through the options.

## Input + orientation  (NOTE: the OPPOSITE of R DESeq2)
- counts: **raw integer counts, samples × genes** (rows = samples!). Bulk count
  tables on disk are usually genes × samples — **transpose first**: `counts = counts.T`.
- metadata: a DataFrame indexed by sample, carrying the design factors.

```python
import pandas as pd
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
from pydeseq2.default_inference import DefaultInference

counts = pd.read_csv(f"{DATA_DIR}/counts.csv", index_col=0)        # genes × samples on disk…
counts = counts.T                                                 # → samples × genes for pydeseq2
counts = counts.loc[:, counts.sum(axis=0) >= 10]                  # drop ~zero genes
metadata = pd.read_csv(f"{DATA_DIR}/samples.csv", index_col=0)    # index aligns to counts.index
```

## Model / design (Wilkinson formula string)
```python
inference = DefaultInference(n_cpus=4)
dds = DeseqDataSet(
    counts=counts, metadata=metadata,
    design="~batch + condition",            # control for batch; interest LAST
    ref_level=["condition", "untreated"],   # control level = reference
    refit_cooks=True, inference=inference,
)
dds.deseq2()        # ← a METHOD on dds. NOT `from pydeseq2.ds import deseq2` (that import does not exist)
```
- Covariate control: `design="~batch + condition"`.
- Continuous covariate: `continuous_factors=["age"]` + include it in the design.
- Interaction: `design="~genotype + condition + genotype:condition"`.

## Contrast → results (Wald)
```python
ds = DeseqStats(dds, contrast=["condition", "treated", "untreated"], inference=inference)
ds.summary()                       # runs the Wald test, builds ds.results_df
res = ds.results_df.sort_values("padj")
ds.lfc_shrink(coeff="condition[T.treated]")   # apeGLM shrinkage for ranking/plots
```
- **Custom contrast:** pass a numpy vector (length = design-matrix width) as
  `contrast=` instead of the 3-element list — e.g. to average levels or pull an
  interaction effect.

## Test options pydeseq2 DOES have
- `lfc_shrink(coeff=...)` — apeGLM shrinkage.
- `alt_hypothesis=` (`"greaterAbs"`/`"greater"`/`"less"`/`"lessAbs"`) with
  `lfc_null=1.0` — test against an LFC threshold instead of 0.
- `cooks_filter`, `independent_filter`, `alpha` — same roles as R DESeq2.
- **No LRT, no ashr/normal shrinkage.** For a multi-level term test / time course,
  or arbitrary-contrast shrinkage, switch to `deseq2-r`.

## Outputs
- `de_results.csv` (full, sorted by padj), `volcano.png` (log2FC vs −log10 padj,
  top-10 labelled), `ma_plot.png` (`ds.plot_MA()`), `top_hits.csv` (padj<0.05 by |log2FC|).

## Caveats
- **Raw counts only** (never TPM/FPKM/normalized).
- Orientation: pydeseq2 wants **samples × genes** — a genes × samples table errors
  ("not of class …" / shape mismatch); transpose first.
- Samples with missing covariates are dropped silently — flag the count.
- Sign depends on the contrast order — confirm "treated vs control" before running.

## In ABA
`ensure_capability("pydeseq2")`, run in `run_python`. Prefer `deseq2-r` when the
session is R-based, the user wants DESeq2/R, or you need LRT / arbitrary contrasts.
