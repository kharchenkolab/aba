---
name: limma-voom
description: Bulk RNA-seq (and array) differential expression with R/Bioconductor limma — voom for counts, flexible custom contrasts, blocking/repeated measures, and fold-change-thresholded tests.
when_to_use: Bulk RNA-seq RAW counts (voom) or already-normalized/log array-like expression (classic limma); want DE between conditions. Prefer THIS over DESeq2 when you need flexible/composite contrasts, many groups, blocking or repeated-measures (random-effect-like) designs, fold-change-thresholded tests (treat), speed on large n, or microarray/normalized data. For a negative-binomial GLM, small-n robustness, or the LRT, see deseq2-r. SCOPE — bulk, microarray, or pseudobulk-aggregated scRNA-seq ONLY; NOT for direct per-cell scRNA-seq DE.
requires_tools: [run_r]
capabilities_needed: [limma, edgeR]
keywords: [limma, voom, edgeR, bulk RNA-seq, microarray, differential expression, contrast, makeContrasts, covariate, batch, donor, block, duplicateCorrelation, treat, quality weights, removeBatchEffect, Bioconductor, R]
produces: [de_results.csv, volcano.png, md_plot.png, logcpm.csv]
domain: genomics
source: "Bioconductor limma User's Guide (Smyth et al.) + F1000 workflow 'RNA-seq analysis is easy as 1-2-3 with limma, Glimma and edgeR' (Law et al.) — bioconductor.org/packages/limma"
---

# Bulk RNA-seq & array DE with R/Bioconductor limma

limma fits gene-wise linear models with empirical-Bayes variance moderation. Its
strengths are **flexible custom contrasts**, **blocking / repeated measures**, and
speed on large designs. For RNA-seq counts use the **voom** path (edgeR builds the
DGEList, voom adds precision weights); for already-normalized/log array-like data
use **classic limma** (eBayes directly). Prefer DESeq2 (`deseq2-r`) when you want a
negative-binomial GLM, small-n robustness, or the LRT.

> **Bulk / pseudobulk only.** voom/limma model **per-sample** expression. In single-cell work
> they apply ONLY to **pseudobulk** (sum raw counts per sample × cell type — see
> **`bp-differential-expression`**), never to a per-cell matrix. For **direct per-cell scRNA-seq
> DE** use `sc.tl.rank_genes_groups` (Wilcoxon; see **`scrna-qc-clustering`** / **`bp-annotation`**)
> or scVI's model-based DE (**`scvi-de`**) — per-cell limma commits pseudoreplication.

**Provision:** `ensure_capability("limma")` **and** `ensure_capability("edgeR")`
(both Bioconductor, via ABA's R layer), then `library(limma); library(edgeR)` in
`run_r`. Heavy on first install; cached after.

## The two choices that DEFINE the analysis — surface them with present_plan
State both explicitly before running (result meaning + sign depend on them). This
is exactly where an advisor should walk the user through the options:
1. **Design / model** — what you adjust for, and the variable you test. Either put
   the variable of interest **LAST** in `~ batch + condition`, or use the
   means-model `~0 + group` and define comparisons via `makeContrasts`.
2. **Contrast** — which levels are compared, in which direction (flips the sign).

## Input + orientation
- counts: **raw integer counts, genes × samples** (rows = genes) — same as DESeq2,
  the OPPOSITE of pydeseq2. NOT TPM/FPKM, NOT logged for the voom path.
- targets: one row per sample, carrying the design factors (condition, batch, donor).

```r
library(limma); library(edgeR)
cts     <- as.matrix(read.csv(file.path(Sys.getenv("DATA_DIR"), "counts.csv"), row.names = 1))  # genes × samples
targets <- read.csv(file.path(Sys.getenv("DATA_DIR"), "samples.csv"), row.names = 1)            # samples × factors
group   <- factor(targets$condition)
batch   <- factor(targets$batch)
stopifnot(all(colnames(cts) == rownames(targets)))    # column/row order MUST align
```

## voom path (RNA-seq counts) — build, filter, normalize
```r
dge  <- DGEList(counts = cts, group = group)
keep <- filterByExpr(dge, group = group)       # auto: ~10 reads in the smallest group
dge  <- dge[keep, , keep.lib.sizes = FALSE]
dge  <- calcNormFactors(dge, method = "TMM")   # TMM library-size normalization
```

## Design / model choices
- **Two-group, treatment-coded:** `design <- model.matrix(~ condition)` — `condition`
  last; the coefficient is treated-vs-reference. Set the reference with
  `group <- relevel(group, ref = "control")`.
- **Control for a covariate** (batch/donor/sex): `design <- model.matrix(~ batch + condition)`
  — `condition` is tested *adjusted* for batch.
- **Continuous covariate** (age, RIN): include the numeric column — `~ age + condition`.
- **Means model (recommended for contrasts):** one coefficient per group, then build
  all comparisons with `makeContrasts`:
```r
design <- model.matrix(~0 + group + batch)      # interest first, covariates after
colnames(design) <- gsub("group", "", colnames(design))
```

## voom → fit → moderate
```r
v    <- voom(dge, design, plot = TRUE)          # log-CPM + mean-variance precision weights
fit  <- lmFit(v, design)
fit  <- eBayes(fit)                             # use this when testing a model coefficient directly
```

## Contrasts — where limma shines (custom / composite)
With the means model, name comparisons explicitly; arbitrary linear combinations are easy:
```r
contr <- makeContrasts(
  TreatedVsControl = treated - control,
  AvgDiseaseVsCtrl = (diseaseA + diseaseB)/2 - control,   # average-of-groups
  Interaction      = (treated.mut - control.mut) - (treated.wt - control.wt),  # diff-of-diffs
  levels = colnames(design))
fit2 <- contrasts.fit(fit, contrasts = contr)
fit2 <- eBayes(fit2)
topTable(fit2, coef = "TreatedVsControl", sort.by = "p", n = Inf)
summary(decideTests(fit2))                       # up/down/notsig per contrast
```

## Blocking / repeated measures (paired or repeated-donor) — a limma strength
For repeated samples on the same donor, treat donor as a **random effect** via
`duplicateCorrelation` (do NOT just add donor as a fixed effect if it's a random
block). Estimate the consensus correlation, then pass `block=`/`correlation=`:
```r
v    <- voom(dge, design, plot = TRUE)
corfit <- duplicateCorrelation(v, design, block = targets$donor)
v    <- voom(dge, design, block = targets$donor, correlation = corfit$consensus)  # re-run with correlation
corfit <- duplicateCorrelation(v, design, block = targets$donor)                  # re-estimate (iterate once)
fit  <- lmFit(v, design, block = targets$donor, correlation = corfit$consensus)
fit  <- contrasts.fit(fit, contrasts = contr); fit <- eBayes(fit)
```

## Fold-change-thresholded test — treat() / topTreat()
`treat` tests whether |log2FC| meaningfully **exceeds** a threshold (not just ≠ 0) —
stricter and better-calibrated than filtering topTable by logFC:
```r
tfit <- treat(fit2, lfc = log2(1.5))             # test |log2FC| > log2(1.5)
res  <- topTreat(tfit, coef = "TreatedVsControl", n = Inf)
summary(decideTests(tfit))
```
The reported p-value is for the threshold test — interpret "significant" as
"confidently beyond the FC threshold", not "FC>threshold AND p<0.05".

## Quality weights — when sample quality varies
Replace `voom` with `voomWithQualityWeights` to down-weight low-quality samples
(combines observation + sample-level weights); for arrays use `arrayWeights()`:
```r
v <- voomWithQualityWeights(dge, design, plot = TRUE)   # then lmFit/contrasts.fit/eBayes as above
```

## Classic limma (already-normalized / log array-like data) — no voom
For microarrays or any matrix of normalized log-expression, fit directly:
```r
fit <- lmFit(logexpr, design)                    # logexpr: genes × samples, normalized + logged
fit <- contrasts.fit(fit, contrasts = contr); fit <- eBayes(fit)
```
**limma-trend** for RNA-seq logCPM (alternative to voom, simpler, OK when library
sizes are similar): `logCPM <- cpm(dge, log = TRUE, prior.count = 3)` →
`fit <- lmFit(logCPM, design)` → `fit <- eBayes(fit, trend = TRUE)`.

## Outputs
```r
res <- res[order(res$adj.P.Val), ]
write.csv(res, file.path(Sys.getenv("DATA_DIR"), "de_results.csv"))   # logFC, AveExpr, t, P.Value, adj.P.Val, B
write.csv(cpm(dge, log = TRUE), file.path(Sys.getenv("DATA_DIR"), "logcpm.csv"))
# plotMD(tfit, column = 1, status = decideTests(tfit)[,1]) for MA/MD; ggplot res for a labelled volcano (logFC vs -log10 adj.P.Val).
```

## Visualization aid — removeBatchEffect (NOT for the model)
For PCA/heatmaps/clustering ONLY, regress out batch from the log-expression:
```r
logCPM_adj <- removeBatchEffect(cpm(dge, log = TRUE), batch = batch, design = model.matrix(~group))
```
Never feed batch-corrected values back into `lmFit` — control for batch *in the
design* instead, so the model accounts for estimation uncertainty.

## Caveats to surface
- **voom needs raw counts** — never TPM/FPKM/already-logged input. Classic limma
  needs normalized + logged input (don't mix the two paths).
- Orientation: **genes × samples** (rows = genes) — a transposed table breaks the fit.
- `treat()` p-values are threshold tests — see the note above; don't double-filter.
- `duplicateCorrelation` (random block) ≠ adding donor as a fixed effect — use it
  for repeated measures / paired-by-donor designs.
- `removeBatchEffect` is for plots only, not for DE testing.
- Contrast direction (`treated - control` vs the reverse) flips the sign — confirm.

## In ABA
`ensure_capability("limma")` + `ensure_capability("edgeR")`, then run every step in
`run_r`. See also **`deseq2-r`** (NB GLM, small-n robustness, LRT) and edgeR's
quasi-likelihood tests — prefer limma-voom for flexible contrasts, many groups,
blocking/repeated measures, speed, or array/normalized data.
