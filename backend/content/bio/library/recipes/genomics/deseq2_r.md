---
name: deseq2-r
description: Authoritative bulk RNA-seq differential expression with R/Bioconductor DESeq2 — designs, covariate control, Wald & LRT tests, LFC shrinkage, and custom contrasts.
when_to_use: Bulk RNA-seq RAW count matrix + sample table; want DE between conditions. Use THIS (R) when the session is already R-based, when the user asks for DESeq2/R, or when the question needs the likelihood-ratio test, multi-factor models, covariate control, interactions, or arbitrary custom contrasts (pydeseq2 is Wald-only). For a Python-only session with no such needs, see bulk-rnaseq-de (pydeseq2). SCOPE — bulk or pseudobulk-aggregated counts ONLY; NOT for direct per-cell scRNA-seq DE.
avoid_when: "Direct per-cell scRNA-seq DE (use rank_genes_groups/Wilcoxon or scVI); NO biological replicates — comparing two clusters within a SINGLE sample pseudobulks to n=1 per group and DESeq2's statistics are then invalid; only a gene list (use enrichment); a Python-only session with no LRT/multi-factor/contrast need (use bulk-rnaseq-de)."
requires_tools: [run_r]
capabilities_needed: [DESeq2]
keywords: [DESeq2, bulk RNA-seq, differential expression, LRT, Wald, contrast, covariate, batch, donor, interaction, lfcShrink, apeglm, ashr, Bioconductor, R]
produces: [de_results.csv, volcano.png, ma_plot.png, normalized_counts.csv]
domain: genomics
source: "Bioconductor DESeq2 vignette (Love, Anders & Huber) — bioconductor.org/packages/DESeq2"
---

# Bulk RNA-seq DE with R/Bioconductor DESeq2

DESeq2 is the reference implementation. Prefer it over pydeseq2 when the session
is already in R, the user asks for DESeq2/R, or the question needs anything
pydeseq2 lacks: the **likelihood-ratio test**, multi-factor models, covariate
control, interaction terms, or arbitrary custom contrasts.

> **Bulk / pseudobulk only.** DESeq2 models gene-level **negative-binomial counts per sample**.
> In single-cell work it applies ONLY to **pseudobulk** (sum raw counts per sample × cell type —
> see **`bp-differential-expression`**), never to a per-cell matrix. For **direct per-cell
> scRNA-seq DE** use `sc.tl.rank_genes_groups` (Wilcoxon; see **`scrna-qc-clustering`** /
> **`bp-annotation`**) or scVI's model-based DE (**`scvi-de`**) — per-cell DESeq2 commits
> pseudoreplication and inflates the FDR.

**Provision:** `ensure_capability("DESeq2")` (Bioconductor, via ABA's R layer),
then `library(DESeq2)` in `run_r`. Heavy on first install; cached after.

## The two choices that DEFINE the analysis — surface them with present_plan
Before running, state both explicitly (the result's meaning + sign depend on them).
This is exactly where an advisor should walk the user through the options:
1. **Design / model** — what you adjust for, and the variable you test. The
   variable of interest goes **LAST**; every term before it is controlled for.
2. **Contrast** — which levels are compared and in which direction.

## Input + orientation
- `countData`: **raw integer counts, genes × samples** (rows = genes, cols =
  samples). NOT TPM/FPKM, NOT normalized, NOT logged.
- `colData`: one row per sample (`rownames(colData) == colnames(countData)`),
  carrying the design factors (condition, batch, donor, …).

```r
library(DESeq2)
cts <- as.matrix(read.csv(file.path(Sys.getenv("DATA_DIR"), "counts.csv"), row.names = 1))  # genes × samples
coldata <- read.csv(file.path(Sys.getenv("DATA_DIR"), "samples.csv"), row.names = 1)        # samples × factors
coldata$condition <- factor(coldata$condition)
coldata$batch     <- factor(coldata$batch)
stopifnot(all(colnames(cts) == rownames(coldata)))   # column/row order MUST align
```

## Reference level (sets the direction)
Put the control level first, so "treated vs untreated" is the natural default:
```r
coldata$condition <- relevel(coldata$condition, ref = "untreated")
```

## Build + pre-filter
```r
dds  <- DESeqDataSetFromMatrix(countData = cts, colData = coldata,
                               design = ~ batch + condition)   # control for batch
keep <- rowSums(counts(dds) >= 10) >= 3        # ≥10 reads in ≥ the smallest group
dds  <- dds[keep, ]
```

## Model / design choices
- **Two-group:** `~ condition`.
- **Control for a covariate** (batch / donor / sex): `~ batch + condition` — the
  nuisance effect is absorbed and `condition` is tested *adjusted* for it.
- **Continuous covariate** (age, RIN): include the numeric column — `~ age + condition`.
- **Paired design** (same donor, two conditions): `~ donor + condition`.
- **Interaction** ("does the condition effect differ by genotype?"):
  `~ genotype + condition + genotype:condition`.

## Test option 1 — Wald (a specific effect, with LFC + direction)
```r
dds <- DESeq(dds)
resultsNames(dds)        # the coefficients you can name
res <- results(dds, name = "condition_treated_vs_untreated")            # by coef name
res <- results(dds, contrast = c("condition", "treated", "untreated"))  # by factor + levels
```

## Test option 2 — LRT (is a whole term / multi-level factor informative)
The LRT compares the full vs a reduced model — use it for a multi-level factor
or a time course, not for one 2-level direction:
```r
dds <- DESeq(dds, test = "LRT", reduced = ~ batch)   # tests the 'condition' term
res <- results(dds)                                  # one p-value/gene for the TERM
```
LRT gives a term-level p-value, **not** a directional LFC — don't report its LFC
column as "the effect".

## Custom contrasts (combine/average levels, interaction effects)
Pass a list (numerator/denominator coefficient names) or a numeric vector over
`resultsNames(dds)`:
```r
# condition effect SPECIFICALLY within genotype B (main + interaction):
res <- results(dds, contrast = list(c("condition_treated_vs_untreated",
                                       "genotypeB.conditiontreated")))
# fully custom: a numeric vector the length of resultsNames(dds)
res <- results(dds, contrast = c(0, 0, 1, -1))
```

## Optional — LFC shrinkage (for ranking / visualization, not significance)
Shrinkage stabilizes noisy log2FCs for low-count genes — handy for ranking and
volcano plots, but it does NOT change which genes are significant, and the raw
`results()` LFCs are the effect sizes to report. Skip it unless ranking/plotting
calls for it; if you do shrink, pick the estimator to fit the comparison (no
single default):
```r
resultsNames(dds)
resLFC <- lfcShrink(dds, coef = "condition_treated_vs_untreated", type = "apeglm")
```
Estimators: **apeglm** (needs a `coef=` name), **ashr** (works with any
`contrast=`), **normal** (simple/built-in). apeglm is in the R base; `ashr` may
need installing; `normal` needs nothing.

## Stricter test against an effect-size threshold
```r
res <- results(dds, lfcThreshold = 1, altHypothesis = "greaterAbs")  # |log2FC| > 1
res <- results(dds, alpha = 0.05)                                    # FDR for indep. filtering
```

## Outputs
```r
res <- res[order(res$padj), ]
write.csv(as.data.frame(res), file.path(Sys.getenv("DATA_DIR"), "de_results.csv"))
write.csv(counts(dds, normalized = TRUE), file.path(Sys.getenv("DATA_DIR"), "normalized_counts.csv"))
# plotMA(res) for MA; ggplot the data.frame for a labelled volcano; vst(dds) for PCA/heatmaps.
```

## Caveats to surface
- **Raw counts only** — never TPM/FPKM/normalized/logged input.
- Direction: "treated vs untreated" vs the reverse flips the LFC sign — confirm.
- LRT ≠ a directional contrast (see above).
- apeglm needs `coef=` (a name); for a custom `contrast=`, shrink with `type="ashr"`.
- Low replication (<3/group) → unreliable; flag it.

## In ABA
`ensure_capability("DESeq2")`, then run every step in `run_r`. If the session is
Python (scanpy/anndata already in play) or the user explicitly wants Python, use
the **`bulk-rnaseq-de`** (pydeseq2) recipe instead — but note pydeseq2 is
Wald-only (no LRT).
