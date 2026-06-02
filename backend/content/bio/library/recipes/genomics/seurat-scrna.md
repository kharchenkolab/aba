---
name: seurat-scrna-v2
description: Generic single-sample scRNA-seq QC + clustering + markers with R/Seurat (v5) — tissue/species-agnostic recipe with data-driven QC thresholds visualized on the cells they remove, PCA elbow showing per-PC and cumulative variance, and a top-5 marker dotplot + canonical-marker FeaturePlot for cluster characterization. SCTransform alternative noted.
when_to_use: scRNA-seq dataset for ANY tissue/organism (10x CellRanger output, .h5, or a counts matrix) and you want QC, clustering, a UMAP, and cluster markers. Use THIS (R/Seurat) when the session is already R-based, when the user asks for Seurat/R, or when downstream work depends on the Bioconductor/Seurat ecosystem. For a Python session (anndata/scanpy already in play), prefer the scanpy equivalent scrna-qc-clustering.
requires_tools: [run_r]
capabilities_needed: [Seurat]
keywords: [Seurat, single cell, scRNA-seq, clustering, UMAP, marker genes, FindAllMarkers, QC, percent.mt, percent.ribo, HVG, PCA, elbow plot, DimHeatmap, DotPlot, FeaturePlot, SCTransform, R, v5]
produces: [qc_violins_pre.png, qc_scatters_pre.png, qc_violins_post.png, hvg_plot.png, pca_elbow.png, pca_heatmap.png, umap_clusters.png, markers_dotplot.png, markers_featureplot.png, cluster_markers.csv, seurat_processed.rds]
domain: genomics
source: "Built from the Seurat PBMC3k guided clustering tutorial + Seurat v5 vignettes (Satija Lab) — satijalab.org/seurat/articles/pbmc3k_tutorial.html — generalized to tissue/species-agnostic guidance with data-driven thresholds."
---

# scRNA-seq single-sample QC + clustering with R/Seurat (v5)

Generic single-sample recipe — works for **any tissue, any organism** for which you
have a counts matrix. We use 10x PBMC as the running example only because it is
familiar; **none of the thresholds, marker lists, or cell-type labels below are
universal** — every concrete number is dataset-dependent and the recipe says, at
each step, what to look at to pick a sensible value for *your* data.

Prefer this (R/Seurat) when the session is already R, the user asks for Seurat,
or downstream tools are Bioconductor. For a Python-native session, use
`scrna-qc-clustering` (scanpy) — same biology, same steps.

**Provision once:** `ensure_capability("Seurat")` (installs into the project R
library; binary install, a few minutes the first time, then cached). Then in
`run_r`:

```r
library(Seurat)
library(ggplot2)   # Seurat returns ggplot objects; attach ggplot2 yourself for ggtitle/aes/ggsave
library(dplyr)     # %>%, group_by, slice_max — for marker post-processing
```

`library(Seurat)` does **not** attach `ggplot2` or `dplyr`, and there is no
`tidyverse` meta-package in this environment — load them by name.

---

## Figure style

The figures in this pipeline share a deliberate visual language. The full code blocks live with each step below, but the choices that distinguish them from Seurat's stock plots are restated here as a checklist — re-read the relevant bullet immediately before you write each figure's code, so the styling survives translation from "I read the recipe" to "I write ggplot2". These are **overrides** for the global figure defaults (`figures.md`); where this section is silent, the global defaults apply.

**Universal**
- Skin every figure with `theme_cowplot()` — the pipeline's figures need to read as one set.
- `ggsave(..., bg = "white", dpi = 120)`. Use `dpi = 180` only for the PCA heatmap (more sub-panels per inch need more pixels).
- Titles 12pt bold (`element_text(size = 12, face = "bold")`); subtitles 9pt grey40 where used.
- Panel grid: major in grey92 (or grey85 on top of darker fills), `linewidth = 0.3`; no minor grid (`panel.grid.minor = element_blank()`).

**Color**
- Diverging signal — anything centered on zero, including DimHeatmap and DotPlot's `avg_log2FC`:
  `scale_*_gradient2(low = "#2166ac", mid = "grey90", high = "#b2182b", midpoint = 0)`. Don't substitute viridis here — viridis is sequential by design and squashes the meaning of "zero." Drop the per-sub-panel legends in DimHeatmap with `& theme(legend.position = "none")` (use `&` not `+` so it broadcasts to every patchwork sub-panel).
- Sequential signal — FeaturePlot, expression-on-UMAP, single-sided positives:
  `scale_colour_gradient(low = "grey85", high = "#b2182b")`. Neutral end near-white, signal in saturated red.

**Dense scatter / jitter** — pre-filter QC scatters, jitter on QC violins
- `alpha = 0.10` (yes, that low — let density carry the visual weight), `size = 0.9` for scatters, `size = 0.20` for violin jitter.
- Two-tone kept/filtered: `scale_colour_manual(values = c(kept = "black", filtered = "red"), guide = guide_legend(override.aes = list(alpha = 1, size = 1.5)))` — the override is essential or the legend dots are invisible.
- Threshold lines: `geom_hline(..., colour = "red", linetype = "dashed", linewidth = 0.5)`.

**QC violins** (pre and post)
- `geom_violin(width = 0.85, colour = "black", linewidth = 0.4, scale = "width", trim = FALSE, alpha = 0.85)`.
- Per-metric fill from a qualitative palette: `scale_fill_brewer(palette = "Set2", guide = "none")` (no legend — the violin shape carries the meaning).
- Facet by metric with `facet_wrap(~ metric, scales = "free_x", ncol = 1, strip.position = "left")`, then `coord_flip()` so metrics read left-to-right at the top.
- Strip styling: `strip.background = element_blank()`, `strip.text.y.left = element_text(angle = 0, hjust = 1, face = "bold")`. Strip the y-axis: `axis.text.y / axis.ticks.y / axis.line.y = element_blank()`. `panel.spacing.y = unit(2, "pt")`.
- Pre-filter adds `geom_jitter` colored by `qc_kept` + the threshold `geom_hline`s; post-filter has neither (everything's kept; no threshold to draw).
- Save: 7×4.7 in (pre) and 7×4.5 in (post).

**HVG plot**
- Re-skin Seurat's `VariableFeaturePlot` with `theme_cowplot()`, then **alpha-poke** the underlying point layer to 0.35 (the HVG MA-style scatter is sparser than the QC scatters; 0.35 reads better than 0.1 here). `VariableFeaturePlot` has no `alpha` argument — walk `p$layers` for `GeomPoint` and set `aes_params$alpha`.
- Label the **top 10** HVGs with `LabelPoints(p_hvg, points = top10, repel = TRUE)` — not top 20; the plot becomes a label soup beyond ten.
- Title: `ggtitle(sprintf("HVG selection (vst, top %d of %d genes); top 10 labeled", ...))`.
- Save 8×5.5 in.

**PCA elbow**
- **One chart, two curves** on the same axis — not two side-by-side panels.
- Per-PC variance: solid blue (`#1f77b4`); cumulative variance: grey dashed (`grey40`). `geom_line(linewidth = 0.6) + geom_point(size = 1.4)`.
- Y-axis as percent of TOTAL HVG-matrix variance (not of the 50 PCs' variance) — `scale_y_continuous(labels = scales::percent_format(accuracy = 1), breaks = seq(0, 0.4, 0.05))`. This makes the cumulative curve plateau at the true fraction the PCs capture (~30% is typical), instead of misleadingly hitting 100%.
- Annotate the chosen `dims` with a red dashed `geom_vline(xintercept = DIMS_CHOSEN, ...)` + a small `annotate("text", ..., "dims = 1:N (chosen)")` to its left.
- Subtitle = heuristic markers: 1%/0.5%/2nd-diff knee — context only, not a decision rule (`element_text(size = 9, colour = "grey40")`).
- Legend inside the plot area, top-right: `legend.position = c(0.98, 0.55)`, `legend.justification = c(1, 0.5)`, semi-transparent background. No legend title.
- Save 7×4.8 in.

**PCA heatmap (DimHeatmap)**
- `DimHeatmap(obj, dims = 1:10, cells = 500, balanced = TRUE, fast = FALSE, combine = TRUE, ncol = 5)` — 2×5 grid uses horizontal space; `fast = FALSE` returns real ggplots so the scale below applies.
- `& scale_fill_gradient2(low = "#2166ac", mid = "grey90", high = "#b2182b", midpoint = 0)` (the diverging palette; `&` not `+` so it broadcasts to every sub-panel).
- `& theme(legend.position = "none")` — drops the 10 identical per-panel legends.
- Larger canvas + denser dpi: `ggsave(..., width = 20, height = 9, dpi = 180)`. Wrap in `suppressMessages()` — ggplot emits one "Scale for fill is already present" per panel; harmless.

**UMAP (DimPlot)**
- `DimPlot(obj, reduction = "umap", label = TRUE, repel = TRUE, pt.size = 0.4) + NoLegend()` — labels live on the embedding, no side legend.
- Title summarizes the run: `ggtitle(sprintf("Louvain res=%.1f · dims=1:%d · n=%d cells · %d clusters", ...))`.
- `coord_fixed()` so distances aren't distorted by aspect ratio.
- Restyle: `theme_cowplot()`, light panel grid (`grey92`, 0.3), axis line in black at 0.4.
- **Alpha-poke** the GeomPoint to 0.6 (DimPlot doesn't expose `alpha`; walk `p$layers`).
- Save 7×6.5 in.

**Dotplot (cluster markers)**
- `DotPlot(obj, features = genes_to_show, cluster.idents = FALSE) + RotatedAxis()` — keep clusters in their numeric order; the rotation handles long gene-name x-axis.
- Diverging palette (same as the heatmap): `scale_colour_gradient2(low = "#2166ac", mid = "grey90", high = "#b2182b", midpoint = 0, name = "avg expr")`.
- Title with marker count: `ggtitle(sprintf("Top 5 markers per cluster (Wilcoxon, only.pos, n=%d genes)", ...))`.
- `axis.text.x = element_text(angle = 60, hjust = 1, size = 8)`; `axis.text.y = element_text(size = 10)`.
- Width scales with gene count so labels don't squash: `width = max(12, 0.18 * length(genes_to_show))`. Height 6.5 in.

**FeaturePlot (canonical lineage markers)**
- 4–8 hand-picked canonical markers (one per major lineage). Don't paint a wall of top-N FeaturePlots — that's the Seurat-tutorial pattern this recipe explicitly avoids.
- `FeaturePlot(obj, features = canonical, order = TRUE, pt.size = 0.3, ncol = 3) & scale_colour_gradient(low = "grey85", high = "#b2182b")` — sequential grey→red.
- `& theme_cowplot() & theme(..., panel.grid.major = element_line(colour = "grey92", linewidth = 0.3), legend.position = "right", legend.key.size = unit(0.4, "cm"))`. Keep axes + grid (gives spatial context vs. the UMAP).
- **Alpha-poke** each panel's GeomPoint to 0.6.
- Height scales with the panel count: `height = max(4, 4 * n_rows)`. Width 13 in.

**Pitfalls to avoid**
- Don't substitute viridis / plasma where the recipe specifies the diverging blue-grey-red — viridis is sequential and erases the meaning of "zero" on signed metrics like `avg_log2FC`.
- Don't strip the panel grid from UMAP / FeaturePlot — the eye uses the axis ticks to read spatial positions.
- Don't drop the alpha-pokes on DimPlot / FeaturePlot / VariableFeaturePlot — Seurat's defaults are opaque dots and the overlap obscures structure.
- Don't put the elbow's per-PC and cumulative curves on separate panels — the comparison only works on a shared y-axis.

---

## The four decisions that define the result

Surface these in your plan (`present_plan`) before running anything. The
defaults given later are *starting points*, not answers.

1. **Species / MT prefix** — `^MT-` for human (uppercase), `^mt-` for mouse,
   `^Mt-` or organism-specific for others. Get this wrong and your %MT is 0
   and you'll keep dying cells.
2. **QC thresholds** — `nFeature_RNA` (low and high) and `percent.mt` cutoffs.
   These define which cells are real. Pick from the distributions, not from a
   prior dataset.
3. **Number of PCs (`dims`)** — feeds the neighbor graph and UMAP. Read off the
   ElbowPlot; 20–30 is a safe default when the elbow is unclear.
4. **Clustering resolution** — `FindClusters(resolution=…)` controls cluster
   count. 0.5 is moderate; 0.2–0.3 coarser, 0.8–1.2 finer.


---

## Step 1 — Load the counts matrix

Three common input shapes; pick by what's on disk.

**10x Cell Ranger output** (a directory with `matrix.mtx[.gz]`, `features.tsv[.gz]`, `barcodes.tsv[.gz]`):

```r
counts <- Read10X(data.dir = "/path/to/filtered_feature_bc_matrix")
```

**10x .h5** (`filtered_feature_bc_matrix.h5`):

```r
counts <- Read10X_h5("/path/to/filtered_feature_bc_matrix.h5")
```

**Plain matrix / `.h5ad` already in memory** — pass a `dgCMatrix` of
genes × cells directly. For an `.h5ad`, convert with `zellkonverter` or read in
Python and hand over the matrix.

Build the Seurat object with **minimal** pre-filtering — keep it permissive so
you can *see* what you're filtering in step 2:

```r
obj <- CreateSeuratObject(
  counts       = counts,
  project      = "<sample_id>",
  min.cells    = 3,    # drop genes seen in <3 cells (sparsity, not biology)
  min.features = 200   # drop barcodes with <200 genes (empty droplets)
)
```

> **Report what the pre-filter removed.** `min.cells=3` and `min.features=200` are sensible defaults, but they're not free — they silently drop genes and droplets. After `CreateSeuratObject`, print the delta so the user sees it:
>
> ```r
> n_genes_raw <- nrow(counts);  n_cells_raw <- ncol(counts)
> n_genes_kept <- nrow(obj);    n_cells_kept <- ncol(obj)
> cat(sprintf("Pre-filter: %d genes x %d cells\n", n_genes_raw, n_cells_raw))
> cat(sprintf("After min.cells=3, min.features=200: %d genes (-%d) x %d cells (-%d)\n",
>             n_genes_kept, n_genes_raw - n_genes_kept,
>             n_cells_kept, n_cells_raw - n_cells_kept))
> ```
>
> If either delta is large (e.g. >20% of cells dropped at load), that's a signal the sample is unusually shallow or the thresholds need revisiting — don't just continue.

`min.features = 200` is a sparse-droplet floor, **not** a QC threshold — real
QC happens in step 2 against the actual distributions.

**Sanity-check the load** before anything else:

```r
dim(obj)                                   # genes × cells
head(rownames(obj))                        # symbols? Ensembl IDs?
sum(grepl("^MT-", rownames(obj)))          # MT gene count — adjust prefix per species
```

If `MT-` returns 0, your prefix is wrong (lowercase `mt-` for mouse; or your
features are Ensembl IDs and you need to match `^ENSG…` mitochondrial IDs via
a gene-info table). **Fix this before computing `percent.mt`** — otherwise every
cell looks healthy and dying cells survive QC.


---

## Step 2 — QC: compute metrics, look at the numbers

Three steps in order: **compute** the metrics, **decide** thresholds from the
quantiles, **then** (in Step 3) plot what you're about to filter so the
visualization shows the consequence of the decision, not the decision itself.

### 2a. Compute QC metrics

```r
# Mitochondrial fraction — adapt the pattern to species
obj[["percent.mt"]]   <- PercentageFeatureSet(obj, pattern = "^MT-")
# Ribosomal protein content — `^Rp[sl]` for mouse, etc.
obj[["percent.ribo"]] <- PercentageFeatureSet(obj, pattern = "^RP[SL]")
```

For non-human: mouse `^mt-` / `^Rp[sl]`; zebrafish `^mt-`. If features are
Ensembl IDs (no symbol pattern works), build the MT/ribo gene sets from a gene
table and pass `features = mt_ids` instead of `pattern`.

### 2b. Read the distributions via quantile tables

Don't plot yet — first look at the numbers so the thresholds are picked from
data, not eyeballed off a plot you might be tempted to match to a prior dataset:

```r
qs_hi <- c(0.50, 0.75, 0.90, 0.95, 0.975, 0.99)
qs_lo <- c(0.005, 0.01, 0.025, 0.05, 0.10)

qtab_hi <- data.frame(
  quantile     = qs_hi,
  nFeature_RNA = quantile(obj$nFeature_RNA, qs_hi),
  nCount_RNA   = quantile(obj$nCount_RNA,   qs_hi),
  percent.mt   = quantile(obj$percent.mt,   qs_hi),
  percent.ribo = quantile(obj$percent.ribo, qs_hi))
print(qtab_hi, row.names = FALSE)

qtab_lo <- data.frame(
  quantile     = qs_lo,
  nFeature_RNA = quantile(obj$nFeature_RNA, qs_lo),
  nCount_RNA   = quantile(obj$nCount_RNA,   qs_lo))
print(qtab_lo, row.names = FALSE)
```

Read off the thresholds from the tables:

- **`nFeature_RNA` floor** — pick from `qs_lo`. The 1st–5th percentile gives the
  empty-droplet shoulder; 200 is a typical floor but tissue-dependent.
- **`nFeature_RNA` ceiling** — pick from `qs_hi`. The 99th percentile usually
  sits where the doublet shoulder begins; 5000–8000 for 10x, lower for low-RNA
  cells (neutrophils, erythrocytes), higher for neurons.
- **`percent.mt` ceiling** — typical defaults: PBMC/blood 5–10%, solid tissue
  15–25%, nuclei (snRNA-seq) ~5% (high MT means whole-cell contamination).
- **`percent.ribo`** — don't filter unless the distribution is visibly bimodal;
  high ribo is biology, not failure.

Write the chosen thresholds into a single small data frame so Step 3 can both
draw them as red dashed lines AND apply them via `subset()`:

```r
THRESH <- list(
  nFeature_low  = 200,
  nFeature_high = 5000,
  mt_high       = 15
  # ribo_high   = NA  # leave NA if not filtering on this metric
)
```

---

## Step 3 — Apply QC: plot the decision, filter, then re-check

### 3a. Pre-filter plots showing the decision

Color each cell by whether it will be kept or filtered, and draw the threshold
values as dashed red horizontal lines. Out-of-spec cells in red (any criterion),
in-spec cells in black — this exposes whether the thresholds are intercepting
the populations you wanted to remove, and whether two criteria are flagging the
same outliers (good) or non-overlapping populations (also informative).

```r
library(tidyr); library(ggplot2); library(cowplot); library(patchwork)

# Single boolean: a cell passes ALL active criteria.
obj$qc_kept <- with(obj@meta.data,
  ifelse(nFeature_RNA > THRESH$nFeature_low  &
         nFeature_RNA < THRESH$nFeature_high &
         percent.mt   < THRESH$mt_high,
         "kept", "filtered"))

# --- pre-filter violin: jitter colored by qc_kept, thresholds drawn as red lines ---
qc_long <- as.data.frame(obj@meta.data[, c("nFeature_RNA","nCount_RNA",
                                           "percent.mt","percent.ribo","qc_kept")])
qc_long$cell <- rownames(qc_long)
qc_long <- pivot_longer(qc_long, c(-cell, -qc_kept),
                        names_to = "metric", values_to = "value")
qc_long$metric <- factor(qc_long$metric,
                         levels = c("percent.ribo","percent.mt",
                                    "nCount_RNA","nFeature_RNA"))

# Threshold values per metric (NA for metrics not filtered on)
thresholds <- data.frame(
  metric = factor(c("percent.ribo","percent.mt","nCount_RNA",
                    "nFeature_RNA","nFeature_RNA"),
                  levels = levels(qc_long$metric)),
  value  = c(NA_real_, THRESH$mt_high, NA_real_,
             THRESH$nFeature_low, THRESH$nFeature_high))

p_vln <- ggplot(qc_long, aes(x = "", y = value)) +
  geom_violin(aes(fill = metric),
              width = 0.85, colour = "black", linewidth = 0.4,
              scale = "width", trim = FALSE, alpha = 0.85) +
  geom_jitter(aes(colour = qc_kept),
              width = 0.32, height = 0, size = 0.20, alpha = 0.10) +
  geom_hline(data = na.omit(thresholds), aes(yintercept = value),
             colour = "red", linetype = "dashed", linewidth = 0.5) +
  facet_wrap(~ metric, scales = "free_x", ncol = 1, strip.position = "left") +
  coord_flip() +
  scale_fill_brewer(palette = "Set2", guide = "none") +
  scale_colour_manual(values = c(kept = "grey15", filtered = "red"),
                      guide = guide_legend(override.aes = list(alpha = 1, size = 1.5))) +
  labs(x = NULL, y = NULL, colour = NULL,
       title = sprintf("QC metrics, pre-filter (n = %d cells)", ncol(obj))) +
  theme_cowplot() +
  theme(strip.placement   = "outside",
        strip.background  = element_blank(),
        strip.text.y.left = element_text(angle = 0, hjust = 1, face = "bold"),
        axis.text.y       = element_blank(),
        axis.ticks.y      = element_blank(),
        axis.line.y       = element_blank(),
        panel.spacing.y   = unit(2, "pt"),
        panel.grid.major.x = element_line(colour = "grey85", linewidth = 0.3),
        panel.grid.major.y = element_blank(),
        panel.grid.minor   = element_blank(),
        plot.title         = element_text(size = 12, face = "bold"),
        legend.position    = "bottom")

ggsave(file.path(WORK_DIR, "qc_violins_pre.png"), p_vln,
       width = 7, height = 4.7, units = "in", dpi = 120, bg = "white")
```

```r
# --- pre-filter scatters: colored by qc_kept, threshold lines in red ---
df <- as.data.frame(obj@meta.data[, c("nCount_RNA","nFeature_RNA",
                                      "percent.mt","qc_kept")])

scatter_theme <- theme_cowplot() +
  theme(panel.grid.major.y = element_line(colour = "grey85", linewidth = 0.3),
        panel.grid.major.x = element_blank(),
        panel.grid.minor   = element_blank(),
        plot.title         = element_text(size = 12, face = "bold"))

s1 <- ggplot(df, aes(nCount_RNA, percent.mt, colour = qc_kept)) +
  geom_point(alpha = 0.1, size = 0.9) +
  geom_hline(yintercept = THRESH$mt_high, colour = "red",
             linetype = "dashed", linewidth = 0.5) +
  scale_colour_manual(values = c(kept = "black", filtered = "red"),
                      guide = guide_legend(override.aes = list(alpha = 1, size = 2))) +
  labs(title = sprintf("nCount vs percent.mt (cutoff %g%%)", THRESH$mt_high),
       x = "nCount_RNA", y = "percent.mt (%)", colour = NULL) +
  scatter_theme

s2 <- ggplot(df, aes(nCount_RNA, nFeature_RNA, colour = qc_kept)) +
  geom_point(alpha = 0.1, size = 0.9) +
  geom_hline(yintercept = c(THRESH$nFeature_low, THRESH$nFeature_high),
             colour = "red", linetype = "dashed", linewidth = 0.5) +
  scale_colour_manual(values = c(kept = "black", filtered = "red"),
                      guide = guide_legend(override.aes = list(alpha = 1, size = 2))) +
  labs(title = sprintf("nCount vs nFeature (cutoffs %g, %g)",
                       THRESH$nFeature_low, THRESH$nFeature_high),
       x = "nCount_RNA", y = "nFeature_RNA", colour = NULL) +
  scatter_theme

p_sc <- (s1 | s2) +
  plot_layout(guides = "collect") +
  plot_annotation(title = "QC scatters, pre-filter",
                  theme = theme(plot.title = element_text(size = 13, face = "bold"))) &
  theme(legend.position = "bottom")

ggsave(file.path(WORK_DIR, "qc_scatters_pre.png"), p_sc,
       width = 12, height = 5.3, units = "in", dpi = 120, bg = "white")
```

### 3b. Apply the filter and report what changed

```r
n_before <- ncol(obj)
obj <- subset(obj, subset =
  nFeature_RNA > THRESH$nFeature_low  &
  nFeature_RNA < THRESH$nFeature_high &
  percent.mt   < THRESH$mt_high)
n_after <- ncol(obj)
cat(sprintf("Cells: %d -> %d  (removed %d, %.1f%%)\n",
            n_before, n_after, n_before - n_after,
            100 * (n_before - n_after) / n_before))
```

If you lose **more than ~20%** of cells, your thresholds are too tight — go
back to Step 2b and reconsider.

### 3c. Post-filter sanity-check plots

Same two figures, no threshold lines (everything's inside them now), no color
split (no "filtered" cells left). The point is to confirm the distributions
look reasonable — no truncated peaks at the cutoff means thresholds didn't bite
into the biological bulk.

```r
qc_long_post <- as.data.frame(obj@meta.data[, c("nFeature_RNA","nCount_RNA",
                                                "percent.mt","percent.ribo")])
qc_long_post$cell <- rownames(qc_long_post)
qc_long_post <- pivot_longer(qc_long_post, -cell,
                             names_to = "metric", values_to = "value")
qc_long_post$metric <- factor(qc_long_post$metric,
                              levels = c("percent.ribo","percent.mt",
                                         "nCount_RNA","nFeature_RNA"))

p_vln_post <- ggplot(qc_long_post, aes(x = "", y = value, fill = metric)) +
  geom_violin(width = 0.85, colour = "black", linewidth = 0.4,
              scale = "width", trim = FALSE, alpha = 0.85) +
  geom_jitter(width = 0.32, height = 0, size = 0.20,
              alpha = 0.10, colour = "grey15") +
  facet_wrap(~ metric, scales = "free_x", ncol = 1, strip.position = "left") +
  coord_flip() +
  scale_fill_brewer(palette = "Set2", guide = "none") +
  labs(x = NULL, y = NULL,
       title = sprintf("QC metrics, post-filter (n = %d cells)", ncol(obj))) +
  theme_cowplot() +
  theme(strip.placement   = "outside",
        strip.background  = element_blank(),
        strip.text.y.left = element_text(angle = 0, hjust = 1, face = "bold"),
        axis.text.y       = element_blank(),
        axis.ticks.y      = element_blank(),
        axis.line.y       = element_blank(),
        panel.spacing.y   = unit(2, "pt"),
        panel.grid.major.x = element_line(colour = "grey85", linewidth = 0.3),
        panel.grid.major.y = element_blank(),
        panel.grid.minor   = element_blank(),
        plot.title         = element_text(size = 12, face = "bold"))

ggsave(file.path(WORK_DIR, "qc_violins_post.png"), p_vln_post,
       width = 7, height = 4.5, units = "in", dpi = 120, bg = "white")
```

(For the post-filter scatter pair, reuse the Step 3a scatter code dropping
the `colour = qc_kept` mapping and the `geom_hline` lines — every cell is now
"kept", no thresholds need showing.)

> **Doublets — when to worry.** For 10x runs with >5% expected doublet rate
> (loading >7k cells per channel) the `nFeature_RNA` cap above catches obvious
> homotypic doublets but misses heterotypic ones. Run **scDblFinder**
> (Bioconductor) or **DoubletFinder** *after* the QC filter, not before — the
> empty droplets confuse the doublet caller.

## Step 4 — Normalize, find variable features, scale

The standard Seurat path. (`SCTransform` is a one-call alternative that
combines all three — see the note at the end of this step.)

```r
obj <- NormalizeData(obj, normalization.method = "LogNormalize",
                     scale.factor = 10000, verbose = FALSE)

obj <- FindVariableFeatures(obj, selection.method = "vst",
                            nfeatures = 2000, verbose = FALSE)

# Sanity-check the HVG list — if MT-/RPL/RPS/MALAT1 dominate, QC was too loose.
top20 <- head(VariableFeatures(obj), 20)
print(top20)
suspect <- grep("^(MT-|RPS|RPL|MALAT1)", top20, value = TRUE)
cat("QC-warning HVGs:", if (length(suspect)) suspect else "none", "\n")
```

HVG mean–variance plot. `VariableFeaturePlot` has no `alpha` argument — we
poke transparency onto its single `GeomPoint` layer post-hoc (public ggplot2
API, no recoding), then add point labels and a `theme_cowplot()` skin so the
plot matches the QC figures.

```r
library(cowplot)

top10 <- head(VariableFeatures(obj), 10)
p_hvg <- VariableFeaturePlot(obj)
for (k in seq_along(p_hvg$layers)) {
  if (inherits(p_hvg$layers[[k]]$geom, "GeomPoint")) {
    p_hvg$layers[[k]]$aes_params$alpha <- 0.35
  }
}
p_hvg <- LabelPoints(p_hvg, points = top10, repel = TRUE, xnudge = 0, ynudge = 0)
p_hvg <- p_hvg +
  ggtitle(sprintf("HVG selection (vst, top %d of %d genes); top 10 labeled",
                  length(VariableFeatures(obj)), nrow(obj))) +
  theme_cowplot() +
  theme(plot.title = element_text(size = 12, face = "bold"),
        panel.grid.major = element_line(colour = "grey92", linewidth = 0.3),
        panel.grid.minor = element_blank())

ggsave(file.path(WORK_DIR, "hvg_plot.png"), p_hvg,
       width = 8, height = 5.5, units = "in", dpi = 120, bg = "white")
```

Then scale — the default (HVGs only) is what you want:

```r
obj <- ScaleData(obj, verbose = FALSE)
# Pass `features = rownames(obj)` ONLY if you need scaled values for a non-HVG
# gene later (e.g. plotting it on a heatmap). Doubles ScaleData time without
# changing PCA results.
```

Regress out unwanted variation only when you have evidence it matters
(e.g. an explicit cell-cycle confound discovered in Step 5). Common candidates
are `percent.mt`, `S.Score`, `G2M.Score`:

```r
# obj <- ScaleData(obj, vars.to.regress = c("percent.mt", "S.Score", "G2M.Score"))
```

> **SCTransform alternative.** `obj <- SCTransform(obj, vars.to.regress="percent.mt",
> verbose=FALSE)` replaces NormalizeData + FindVariableFeatures + ScaleData.
> Often better for very heterogeneous data (large dynamic range across cell
> types). After SCT, `DefaultAssay(obj) <- "SCT"` and pass `assay="SCT"` to
> downstream steps; for marker DE switch back to `assay="RNA"` (with
> NormalizeData run once on it) — or use `PrepSCTFindMarkers` if staying on
> SCT.

---

## Step 5 — Linear dimensionality reduction (PCA) and choosing `dims`

```r
obj <- RunPCA(obj, features = VariableFeatures(obj), npcs = 50, verbose = FALSE)
```

**Pick `dims` once and stick with it.** Seurat's official guidance: there is
no automatic rule. The PBMC3k tutorial recommends "JackStraw, or eyeball the
ElbowPlot, or just take a generous default and don't worry". UMAP and Leiden
are robust to a few extra noisy PCs but suffer when you cut too few — so
**default to `DIMS_CHOSEN = 30`** (Seurat v5's routine choice in current
vignettes) and let the user override if they have a reason. The elbow plot
below is a *diagnostic*, not an automatic threshold.

### 5a. Which genes drive each PC

Sanity-check the top components are biology, not technical (MT, ribo, stress,
or cell cycle dominating PC1 means a confound to address in Step 4 — see the
`vars.to.regress` note there).

```r
print(obj[["pca"]], dims = 1:5, nfeatures = 8)
```

### 5b. Elbow plot — variance explained, per-PC and cumulative

Two curves on one axis: per-PC proportion of variance (blue solid) and
cumulative (grey dashed). Both are normalized to the **total variance of the
HVG matrix** (not to the variance the 50 PCs alone explain) — so the
cumulative curve plateaus at the true fraction the PCs capture (~30% is
typical for a single 10x sample on 2000 HVGs; the rest is dispersed across
thousands of small dimensions).

```r
library(tidyr); library(cowplot); library(ggplot2)

DIMS_CHOSEN <- 30   # default; override if you have a reason

# Compute proportions against the TRUE total HVG-matrix variance
sd_mat    <- GetAssayData(obj, layer = "scale.data")
total_var <- sum(apply(sd_mat, 1, var))
var_pc    <- Stdev(obj, reduction = "pca")^2
prop      <- var_pc / total_var
cum       <- cumsum(prop)
df_elb    <- data.frame(PC = seq_along(prop), prop = prop, cum = cum)
df_long   <- pivot_longer(df_elb, c(prop, cum),
                          names_to = "kind", values_to = "value")
df_long$kind <- factor(df_long$kind, levels = c("prop","cum"),
                       labels = c("per-PC","cumulative"))

# Heuristic markers — context only, not used to pick DIMS_CHOSEN
pc_1pct    <- max(which(df_elb$prop >= 0.010), na.rm = TRUE)
pc_05pct   <- max(which(df_elb$prop >= 0.005), na.rm = TRUE)
pc_kneedle <- {
  v <- log(df_elb$prop[seq_len(min(30, nrow(df_elb)))])
  which.max(diff(diff(v))) + 1
}
caption <- sprintf(
  "heuristics: >=1%% per-PC -> PC %d   |   >=0.5%% per-PC -> PC %d   |   2nd-diff knee -> PC %d",
  pc_1pct, pc_05pct, pc_kneedle)

p_elbow <- ggplot(df_long, aes(x = PC, y = value,
                               colour = kind, linetype = kind)) +
  geom_vline(xintercept = DIMS_CHOSEN, colour = "red",
             linetype = "dashed", linewidth = 0.5) +
  annotate("text", x = DIMS_CHOSEN - 0.7, y = max(df_long$value) * 0.95,
           label = sprintf("dims = 1:%d (chosen)", DIMS_CHOSEN),
           colour = "red", hjust = 1, size = 3.4) +
  geom_line(linewidth = 0.6) +
  geom_point(size = 1.4) +
  scale_y_continuous(labels = scales::percent_format(accuracy = 1),
                     breaks = seq(0, 0.4, 0.05),
                     limits = c(0, max(df_long$value) * 1.05)) +
  scale_x_continuous(breaks = c(1, seq(5, 50, 5))) +
  scale_colour_manual(values = c("per-PC" = "#1f77b4",
                                 "cumulative" = "grey40")) +
  scale_linetype_manual(values = c("per-PC" = "solid",
                                   "cumulative" = "dashed")) +
  labs(title = "PCA variance explained (of total HVG-matrix variance)",
       subtitle = caption,
       x = "principal component",
       y = "proportion of total variance",
       colour = NULL, linetype = NULL) +
  theme_cowplot() +
  theme(plot.title = element_text(size = 12, face = "bold"),
        plot.subtitle = element_text(size = 9, colour = "grey40"),
        panel.grid.major.y = element_line(colour = "grey92", linewidth = 0.3),
        panel.grid.major.x = element_blank(),
        panel.grid.minor = element_blank(),
        legend.position = c(0.98, 0.55),
        legend.justification = c(1, 0.5),
        legend.background = element_rect(fill = alpha("white", 0.5), colour = NA),
        legend.key = element_blank(),
        legend.title = element_blank())

ggsave(file.path(WORK_DIR, "pca_elbow.png"), p_elbow,
       width = 7, height = 4.8, units = "in", dpi = 120, bg = "white")
```

**Beware first-PC artifacts.** If PC1 is driven by `MT-` genes, ribosomal
genes, or cell-cycle markers (visible in 5a's printed loadings), that means
you'll project a covariate onto every downstream layout. Options: tighten QC,
regress the offending score in `ScaleData` (see Step 4), or exclude that PC
from `dims` (e.g. `dims = 2:30`).

### 5c. Optional — DimHeatmap

Visualizes each PC's top-loading cells × top genes, sorted by PC score. Useful
to confirm that the first 5–10 PCs separate biology cleanly, and to spot the
PC where the structure becomes noise (informs whether you need 20 or 30).

Two non-defaults worth knowing about:

- **`fast = FALSE`** so each panel is a real `ggplot` — needed for color
  customization (the `fast = TRUE` default uses `image()` with a hard-coded
  purple/black/yellow palette and ignores ggplot scales).
- **`ncol = 5, dims = 1:10`** lays out 10 PCs as 2 rows × 5 columns — better
  use of horizontal space than the default 3 × 3.

`& scale_fill_gradient2(...)` broadcasts a diverging blue/grey/red palette
across every sub-panel (use `&` not `+` so it hits all patchwork panels), and
`& theme(legend.position = "none")` drops the 10 identical per-panel legends.

```r
library(patchwork)

p_dimheat <- DimHeatmap(obj, dims = 1:10, cells = 500,
                        balanced = TRUE, fast = FALSE, combine = TRUE,
                        ncol = 5) &
  scale_fill_gradient2(low = "#2166ac", mid = "grey90", high = "#b2182b",
                       midpoint = 0) &
  theme(legend.position = "none")

# 20 x 9 in @ 180 dpi gives the gene labels enough room to stay clear.
# ggplot will emit one "Scale for fill is already present" message per panel
# replaced — harmless; wrap in suppressMessages() if it bothers you.
suppressMessages(
  ggsave(file.path(WORK_DIR, "pca_heatmap.png"), p_dimheat,
         width = 20, height = 9, units = "in", dpi = 180, bg = "white")
)
```

> **Rigorous alternative — JackStraw.** `JackStraw(obj, num.replicate = 100)`
> then `ScoreJackStraw(obj, dims = 1:50)` runs a permutation test and gives a
> per-PC p-value. Keep PCs with `p < 0.05`. Slow (minutes for thousands of
> cells); use only if you need to defend the choice in a paper.


## Step 6 — Neighbor graph, clustering, UMAP

```r
DIMS_CHOSEN <- 30   # from Step 5

obj <- FindNeighbors(obj, dims = 1:DIMS_CHOSEN, verbose = FALSE)
obj <- FindClusters(obj, resolution = 0.5, verbose = FALSE)   # Louvain (algorithm = 1)
obj <- RunUMAP(obj, dims = 1:DIMS_CHOSEN, verbose = FALSE)

cat("Clusters:", length(levels(Idents(obj))), "\n")
print(table(Idents(obj)))
```

UMAP colored by cluster, labels drawn on the embedding. We restyle on
`theme_cowplot()` for consistency with the QC + HVG + elbow figures, and
poke `alpha = 0.6` onto the underlying `GeomPoint` layer (DimPlot exposes
`pt.size` but not `alpha`).

```r
library(cowplot); library(ggplot2)

p_umap <- DimPlot(obj, reduction = "umap", label = TRUE, repel = TRUE,
                  pt.size = 0.4) +
  ggtitle(sprintf("Louvain res=0.5 · dims=1:%d · n=%d cells · %d clusters",
                  DIMS_CHOSEN, ncol(obj), length(levels(Idents(obj))))) +
  NoLegend() +
  theme_cowplot() +
  theme(plot.title = element_text(size = 12, face = "bold"),
        panel.grid.major = element_line(colour = "grey92", linewidth = 0.3),
        panel.grid.minor = element_blank(),
        axis.line = element_line(colour = "black", linewidth = 0.4)) +
  coord_fixed()

for (k in seq_along(p_umap$layers)) {
  if (inherits(p_umap$layers[[k]]$geom, "GeomPoint")) {
    p_umap$layers[[k]]$aes_params$alpha <- 0.6
  }
}

ggsave(file.path(WORK_DIR, "umap_clusters.png"), p_umap,
       width = 7, height = 6.5, units = "in", dpi = 120, bg = "white")
```

Notes that bite in practice:

- `FindClusters` defaults to **Louvain** (`algorithm = 1`). For Leiden,
  pass `algorithm = 4` (requires `leidenalg`/`reticulate`). Pick *one* and
  report it; don't silently change between calls.
- Resolution **does not preserve cluster identity**. Cluster 3 at res=0.5 is
  not "cluster 3" at res=0.8. Pick one resolution per analysis and stick
  with it through marker calling and labeling.
- **`RunUMAP` defaults changed in Seurat v5**: now `uwot` with cosine metric
  (was `umap-learn` via reticulate with euclidean/correlation). Layouts are
  not identical across versions — pin the Seurat version if reproducibility
  across machines matters. To revert to the old behavior:
  `RunUMAP(obj, dims=…, umap.method="umap-learn", metric="correlation")`.
- **UMAP is a visualization, not the clustering**. Don't redefine clusters
  by drawing polygons on the UMAP — read off `obj$seurat_clusters`.
- **Seurat's `RunUMAP` defaults (`n.neighbors = 30, min.dist = 0.3`) differ
  from scanpy's (`15` and `0.5`)** — same data, different embeddings. If
  comparing across pipelines, match these knobs explicitly.

### When to consider integration instead

If the sample is one library, this single-sample recipe is the whole story.
For multiple samples / batches: run this recipe per-sample first to sanity-
check each, then integrate (Seurat `IntegrateLayers` /
`harmony` / `scVI`). Integrating *before* per-sample QC hides batch-specific
quality problems.

## Step 7 — Cluster markers

`FindAllMarkers` runs a per-cluster vs.-rest Wilcoxon test. Defaults below
match the recipe text from the Satija PBMC3k tutorial.

```r
markers <- FindAllMarkers(obj,
                          only.pos        = TRUE,
                          min.pct         = 0.25,    # in >=25% of cluster cells
                          logfc.threshold = 0.25,    # log2FC >= 0.25
                          verbose         = FALSE)

top5 <- markers %>%
        group_by(cluster) %>%
        slice_max(order_by = avg_log2FC, n = 5) %>%
        ungroup()

write.csv(markers,
          file = file.path(WORK_DIR, "cluster_markers.csv"),
          row.names = FALSE)

cat("total significant markers:", nrow(markers), "\n")
print(top5 %>% select(cluster, gene, avg_log2FC, pct.1, pct.2, p_val_adj), n = Inf)
```

> **Speed.** Seurat's default Wilcoxon is slow on large objects. Install
> `presto` once (`devtools::install_github('immunogenomics/presto')`) and
> Seurat will use it automatically — typically 10x faster, identical results.

### Two figures — dotplot and lineage-marker FeaturePlot

**Don't draw a wall of FeaturePlots covering the full top-N marker list.**
That's the original Seurat-tutorial output pattern and it overwhelms more
than it informs. Two complementary figures:

1. **Dotplot** of top 5 markers per cluster x cluster grid — compact,
   comparable, the right default for "what marks each cluster?"
2. **FeaturePlot small-multiple** of 4–8 hand-picked **canonical lineage
   markers** (one per major population) — gives spatial context on the UMAP.
   Pick by biology, not by top-N.

```r
# --- Figure 1: dotplot, top 5 markers per cluster ---
genes_to_show <- unique(top5$gene)

p_dot <- DotPlot(obj, features = genes_to_show, cluster.idents = FALSE) +
  RotatedAxis() +
  scale_colour_gradient2(low = "#2166ac", mid = "grey90", high = "#b2182b",
                         midpoint = 0, name = "avg expr") +
  ggtitle(sprintf("Top 5 markers per cluster (Wilcoxon, only.pos, n=%d genes)",
                  length(genes_to_show))) +
  theme_cowplot() +
  theme(plot.title = element_text(size = 12, face = "bold"),
        axis.text.x = element_text(angle = 60, hjust = 1, size = 8),
        axis.text.y = element_text(size = 10),
        panel.grid.major = element_line(colour = "grey92", linewidth = 0.3),
        panel.grid.minor = element_blank())

# Width scales with number of genes (10-20 clusters x ~5 markers => 50-100 genes).
suppressMessages(
  ggsave(file.path(WORK_DIR, "markers_dotplot.png"), p_dot,
         width = max(12, 0.18 * length(genes_to_show)), height = 6.5,
         units = "in", dpi = 120, bg = "white")
)
```

```r
# --- Figure 2: FeaturePlot, canonical lineage markers ---
# Replace this list with the canonical markers for YOUR tissue/organism.
# Examples below are PBMC; for brain use NEUROD2/SLC17A7/GAD1/AQP4/MOG/CX3CR1
# or whatever your reference atlas suggests.
canonical <- c("CD3D","CD8A","MS4A1","GNLY","LYZ","PPBP")
canonical <- canonical[canonical %in% rownames(obj)]

p_feat <- FeaturePlot(obj, features = canonical, order = TRUE,
                      pt.size = 0.3, ncol = 3) &
  scale_colour_gradient(low = "grey85", high = "#b2182b") &
  theme_cowplot() &
  theme(plot.title = element_text(size = 11, face = "bold"),
        panel.grid.major = element_line(colour = "grey92", linewidth = 0.3),
        panel.grid.minor = element_blank(),
        legend.position = "right",
        legend.key.size = unit(0.4, "cm"))

# Alpha-poke for each panel's GeomPoint (FeaturePlot lacks alpha arg)
for (i in seq_along(p_feat)) {
  pl <- p_feat[[i]]
  if (!is.null(pl$layers)) {
    for (k in seq_along(pl$layers)) {
      if (inherits(pl$layers[[k]]$geom, "GeomPoint")) {
        pl$layers[[k]]$aes_params$alpha <- 0.6
      }
    }
    p_feat[[i]] <- pl
  }
}

# Height scales with number of panels: 4 in tall per row, 3 panels per row.
n_rows <- ceiling(length(canonical) / 3)
suppressMessages(
  ggsave(file.path(WORK_DIR, "markers_featureplot.png"), p_feat,
         width = 13, height = max(4, 4 * n_rows),
         units = "in", dpi = 120, bg = "white")
)
```

### Tests and what they cost

`FindAllMarkers(test.use = ...)`:

- `wilcox` (default) — fast, non-parametric, the right default
- `MAST` — zero-inflated; useful for differential **state** within a cluster
- `DESeq2` / `negbinom` — slow; rarely needed for marker discovery
- `roc` — gives a per-gene classifier AUC (interpretable; nice for picking
  the "cleanest" marker)

### Annotation — biology, not numbers

Cluster labels come from biology. Two paths:

1. **Manual** — check the top markers against a curated panel for your
   tissue (PanglaoDB, CellMarker, or a published atlas for the same organ).
   Re-label with `obj <- RenameIdents(obj, "0" = "CD14+ mono", ...)`.
2. **Automated reference mapping** — `SingleR`, `Azimuth`, or `scArches` if a
   reference exists for your tissue/organism. See the
   `scvi-reference-mapping` skill.

**Do not invent cell-type names from a single marker.** "FCGR3A+ monocytes"
needs FCGR3A *plus* the rest of the non-classical-monocyte signature.

## Step 8 — Save the processed object

```r
rds_path <- file.path(WORK_DIR, "seurat_processed.rds")
saveRDS(obj, file = rds_path)

# Verify the write — size and round-trip.
cat(sprintf("Wrote %s (%.1f MB)\n", rds_path, file.info(rds_path)$size / 1e6))
obj_check <- readRDS(rds_path)
stopifnot(identical(dim(obj_check), dim(obj)))
rm(obj_check); invisible(gc())
```

The saved `.rds` carries everything the recipe produced — the `RNA` assay
(`counts`, `data`, `scale.data` layers), the `pca` and `umap` reductions,
the `RNA_nn` / `RNA_snn` graphs, the `seurat_clusters` column, and the 2000
HVGs. A follow-up session can pick up at Step 7 (markers, sub-clustering,
annotation) without re-running 1–6.

**Faster I/O on large objects.** `qs::qsave(obj, "seurat_processed.qs")` is
2–3x faster than `saveRDS` for big objects and produces smaller files;
`qs::qread` to load. Install once with `install.packages('qs')`.

**Handing off to scanpy / Python.** Use `zellkonverter` (Bioconductor) via
the `SingleCellExperiment` bridge — actively maintained, no Python install
needed, produces a standard `.h5ad`:

```r
# install once: BiocManager::install("zellkonverter")
sce <- Seurat::as.SingleCellExperiment(obj)
zellkonverter::writeH5AD(sce, file = file.path(WORK_DIR, "seurat_processed.h5ad"))
```

A lighter alternative is `sceasy::convertFormat(obj, from="seurat", to="anndata", outFile=...)`,
but it pulls in `reticulate` + Python `anndata`.

> **Note: base Seurat does not ship an `.h5ad` writer.** `library(Seurat)`
> exports only `Read10X_h5` for HDF5 I/O. The older `SeuratDisk` package
> (`SaveH5Seurat` / `Convert`) is GitHub-only and currently dormant — avoid
> it in new pipelines. Seurat v5's own on-disk format (`BPCells`-backed
> assays) is a **storage backend for very large objects you want to keep on
> disk within R**, not a cross-tool exchange format.


## Common pitfalls — quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| All cells have `percent.mt ≈ 0` | MT prefix wrong for species or features are Ensembl IDs | Match `^mt-` (mouse) / build MT set from a gene table |
| HVGs dominated by MT/RPL/RPS/MALAT1 | QC too loose, OR you want to mask these | Tighten QC; or remove these gene families before `FindVariableFeatures` |
| One huge cluster, no structure | Wrong `dims` or too few HVGs; or a strong batch | Re-check ElbowPlot; consider integration if multi-sample |
| Two parallel "arms" of one cell type | Often interferon / stress response (IFI27, ISG15, IFIT*) | Score the IFN signature; decide whether to keep or regress |
| Clusters split by `nCount_RNA` | A QC variable is the dominant axis | Tighten QC or regress `nCount_RNA`/`percent.mt` in ScaleData |
| `FindAllMarkers` is very slow | Default Wilcox on many genes × many cells | Pre-subset with `min.pct`/`logfc.threshold`; consider `presto` (auto-used in Seurat v5 if installed) |
| Cluster numbers reshuffle between runs | Different resolution OR you re-ran `FindClusters` with new params | Pin a resolution; save the object after clustering |
| Seurat ↔ scanpy clusters disagree (ARI ~0.4–0.6) | Expected — different HVG selection, normalization, neighbor graph | Compare via contingency + marker Jaccard, not 1:1 label matching |

---

## What this recipe deliberately does NOT cover

- **Multi-sample integration** — use `harmony-integration-scanpy` (Python) or
  Seurat `IntegrateLayers` / Harmony / scVI (R). Always per-sample QC first.
- **Doublet calling** — see scDblFinder / DoubletFinder; mention in the QC step.
- **Trajectory / pseudotime** — Monocle3, Slingshot, PAGA.
- **Reference-based cell-type annotation** — SingleR, Azimuth, scArches
  (`scvi-reference-mapping`).
- **CITE-seq / multimodal** — Seurat WNN; out of scope here.

Each of these is a separate skill or a separate plan; do not silently fold them
into a "single-sample QC" run.
