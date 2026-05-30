---
name: harmony-integration
description: Batch/sample integration of scRNA-seq with R/Seurat + Harmony — merge multiple samples, preprocess (Normalize→HVG→Scale→PCA), RunHarmony over a batch covariate, then cluster/UMAP on the harmony reduction and assess mixing.
when_to_use: Two or more scRNA-seq samples/conditions/donors (10x lanes, stim vs ctrl, batches) whose batch effect is visible in a plain PCA/UMAP, and you want a fast, linear batch correction before clustering/annotation. Use THIS (R/Seurat) when the session is already R-based or the user asks for Seurat/Harmony in R. For a Python/scanpy session use harmony-integration-scanpy (the full scanpy + harmonypy flow via sc.external.pp.harmony_integrate), or create-harmony-embeddings-scrna for the embedding-only step. For a deep-generative alternative see scvi-integration. For a single clean sample no integration is needed — see seurat-scrna / scrna-qc-clustering.
requires_tools: [run_r]
capabilities_needed: [harmony, Seurat]
keywords: [Harmony, RunHarmony, batch correction, batch integration, sample integration, group.by.vars, theta, sigma, lambda, Seurat, scRNA-seq, single cell, reduction harmony, UMAP, FindClusters, batch mixing, harmonypy, R]
produces: [integrated_umap.png, harmony_clusters.csv, integrated.rds]
domain: genomics
source: "Dave Tang's MUSE notes — Harmony integration with Seurat (R 4.5.0, Seurat 5.3.0, harmony 1.2.3): davetang.github.io/muse/harmony.html"
---

# scRNA-seq batch integration with R/Seurat + Harmony

Harmony is a fast, linear batch-correction method: it iteratively soft-clusters
cells in PCA space and learns a correction that pulls the same cell types from
different batches together while **leaving the expression matrix untouched** — it
corrects an *embedding*. You merge your samples into one object, run the standard
Seurat preprocessing through `RunPCA`, hand the PCA to `RunHarmony` naming the
batch covariate, and then run every downstream step (`FindNeighbors`, `RunUMAP`)
on `reduction = "harmony"` instead of `"pca"`.

**Provision:** `ensure_capability("Seurat")` and `ensure_capability("harmony")`
(both R packages in ABA's R layer — heavy on first install, cached after), then
in `run_r`:
```r
suppressPackageStartupMessages({
  library(Seurat)
  library(harmony)     # provides RunHarmony() — see note below
  library(tidyverse)
  library(patchwork)
})
packageVersion("harmony")   # tutorial: '1.2.3'  (Seurat 5.3.0, R 4.5.0)
```

> **Which `RunHarmony`?** Modern `harmony` (≥1.0) ships its **own** `RunHarmony`
> S3 method for Seurat objects, so `library(harmony)` is all you need — this is
> what the tutorial uses. The old route (`SeuratWrappers::RunHarmony`) is
> deprecated; if you accidentally load SeuratWrappers, its `RunHarmony` shadows
> harmony's and arg names differ — prefer the `harmony` package directly.

## The choices that DEFINE the integration — surface them with present_plan
Halt and walk the user through these before committing; this is exactly where an
advisor adds value, because over-integration silently erases real biology.
1. **What to integrate over (`group.by.vars`)** — the covariate(s) you want the
   batch effect removed *for*. Integrate over the **technical** nuisance (sample,
   donor, lane, batch, 10x run), **not** over the biological variable you care
   about. If `stim`/`ctrl` IS the question, integrating over it would wash out
   the very signal you're studying — see the trade-off below.
2. **Number of PCs (`dims`)** — how many PCs feed Harmony and the downstream
   graph/UMAP. The same `dims` (here `1:20`) flow through `RunHarmony`'s `npcs`,
   `RunUMAP`, and `FindNeighbors`.
3. **Clustering resolution** — `FindClusters(resolution = ...)` sets cluster
   count, as in any Seurat run.

## Integrate-in-the-design vs regress-out — the key conceptual choice
Harmony **integrates over** a covariate: it aligns batches in the embedding so
that shared cell states overlap, *without* assuming the batch effect is a single
additive vector. Contrast with **regressing out** a covariate (e.g.
`ScaleData(vars.to.regress=...)` or DESeq2's `~ batch + condition`), which removes
a linear effect from every gene. Rule of thumb:
- **Integrate** (`group.by.vars`) when batches contain the *same* populations and
  you just need them to co-embed (donor/lane/technology effects) → use Harmony.
- **Regress out / put in the design** when you have a measured continuous nuisance
  (cell-cycle score, %mito) or you want a per-gene model that *keeps* the variable
  estimable for DE → keep it in the model, don't integrate it away.
Never integrate over the variable you intend to test — you'd remove the effect
and then "discover" there is none.

## 1. Load each sample → merge into ONE object
Build one Seurat object spanning all samples and record the batch covariate in
`meta.data`. The tutorial cbinds two count matrices (stim + ctrl) and tags each
cell; for many samples, `merge()` per-sample `CreateSeuratObject`s.
```r
# two-sample form (tutorial): cbind raw counts, then label the batch covariate
pbmc <- CreateSeuratObject(counts = cbind(pbmc.stim, pbmc.ctrl),
                           project = "Kang", min.cells = 5)
pbmc@meta.data$stim <- c(rep("STIM", ncol(pbmc.stim)),
                         rep("CTRL", ncol(pbmc.ctrl)))

# many-sample form: load each, then merge (add.cell.ids keeps barcodes unique)
# objs <- lapply(sample_dirs, \(d) CreateSeuratObject(Read10X(d), min.cells = 3))
# pbmc <- merge(objs[[1]], objs[-1], add.cell.ids = names(sample_dirs))
# pbmc$batch <- pbmc$orig.ident          # whatever column names your samples
```

## 2. Standard preprocessing → PCA (exactly as a single sample)
Normalize, pick variable features, scale, PCA — Harmony runs *on the PCA*. The
tutorial takes the **union** of per-condition HVGs so each sample's variable
genes are represented (a robustness touch; plain `FindVariableFeatures` on the
merged object is the simpler default).
```r
pbmc <- NormalizeData(pbmc, verbose = FALSE)
pbmc <- FindVariableFeatures(pbmc, selection.method = "vst", nfeatures = 2000)

# tutorial refinement — union of HVGs computed within each condition:
cell_by_cond <- split(rownames(pbmc@meta.data), pbmc@meta.data$stim)
vfeatures <- lapply(cell_by_cond, function(cells)
  FindVariableFeatures(pbmc[, cells], selection.method = "vst", nfeatures = 2000) |>
    VariableFeatures())
VariableFeatures(pbmc) <- unique(unlist(vfeatures))

pbmc <- ScaleData(pbmc, verbose = FALSE) |>
  RunPCA(features = VariableFeatures(pbmc), npcs = 20, verbose = FALSE)
```

## 3. RunHarmony — the integration step (verbatim from the tutorial)
This is the `harmony` package's `RunHarmony`. The **second positional argument is
`group.by.vars`** — the batch covariate name(s) in `meta.data` (`"stim"` here).
```r
pbmc <- RunHarmony(pbmc, "stim", plot_convergence = TRUE,
                   nclust = 50, max_iter = 10, early_stop = TRUE)
```
- `"stim"` → `group.by.vars`: integrate over this covariate. Pass a vector
  `c("donor","lane")` to correct for several at once.
- `plot_convergence = TRUE` — diagnostic plot of the objective per iteration;
  it should flatten (converge).
- `nclust = 50` — number of soft clusters Harmony uses internally.
- `max_iter = 10`, `early_stop = TRUE` — iteration cap and stop-on-convergence.
- Adds a new reduction named `"harmony"` to the object (the corrected embedding,
  same dim count as the PCA you fed it).

## 4. Downstream — run everything on `reduction = "harmony"`
The *only* change vs an unintegrated workflow: point neighbors/UMAP/tSNE at the
`"harmony"` reduction instead of the default `"pca"`. Use the same `dims` as PCs.
```r
pbmc <- FindNeighbors(pbmc, reduction = "harmony", dims = 1:20) |>
  FindClusters(resolution = 0.5)
pbmc <- RunUMAP(pbmc, reduction = "harmony", dims = 1:20)
# pbmc <- RunTSNE(pbmc, reduction = "harmony")   # tSNE works the same way
```

## 5. Assess batch mixing — did it work?
Integration succeeds when cells from different batches **interleave** within
shared cell types yet **distinct cell types stay separate**. Compare a UMAP
colored by batch (should be well-mixed) against one colored by cluster (should
show clean populations); also eyeball the harmony embedding directly.
```r
# raw harmony embedding by batch (sanity check straight after RunHarmony)
DimPlot(pbmc, reduction = "harmony", group.by = "stim", pt.size = .1)
VlnPlot(pbmc, features = "harmony_1", group.by = "stim", pt.size = .1)

# the real test — UMAP by batch vs by cluster, side by side
p1 <- DimPlot(pbmc, reduction = "umap", group.by = "stim", pt.size = .1)  # want: mixed
p2 <- DimPlot(pbmc, reduction = "umap", label = TRUE,    pt.size = .1)    # want: clean clusters
p1 + p2
```
Read it: if batches still form separate islands per cell type, integration is too
weak (raise `theta`); if biologically distinct types have collapsed together, it's
too aggressive (lower `theta`). Compare against the *pre*-Harmony UMAP (run on
`reduction = "pca"`) to confirm there was a batch effect to fix in the first place.

## Tuning knobs — theta / sigma / lambda
Defaults are good; reach for these only when mixing diagnostics demand it.
- **`theta`** — diversity penalty per corrected covariate; **default `2`**.
  Higher = more aggressive mixing (forces batches together); lower = gentler,
  preserves more batch-specific structure. The first dial to turn.
- **`sigma`** — soft-clustering assignment width; **default `0.1`**. Larger
  values let cells contribute to more distant clusters (softer assignment).
- **`lambda`** — ridge penalty on the correction; controls how strongly cells are
  pulled toward the batch-corrected centroid. Lower = stronger correction.

## Outputs
```r
write.csv(data.frame(cell = colnames(pbmc),
                     batch = pbmc$stim, cluster = Idents(pbmc)),
          file.path(Sys.getenv("DATA_DIR"), "harmony_clusters.csv"), row.names = FALSE)
ggplot2::ggsave(file.path(Sys.getenv("ARTIFACTS_DIR"), "integrated_umap.png"), p1 + p2,
                width = 12, height = 5)
saveRDS(pbmc, file.path(Sys.getenv("DATA_DIR"), "integrated.rds"))   # resume later
```
For markers/DE on the integrated clusters, follow **seurat-scrna**: in Seurat v5
the per-sample `counts.*` layers are split after `merge()`, so
`pbmc[["RNA"]] <- JoinLayers(pbmc[["RNA"]])` before `FindAllMarkers` /
`FindMarkers`. DE uses the joined expression — **not** the harmony embedding (the
embedding is for neighbors/UMAP only).

## Caveats to surface
- **Harmony corrects an embedding, not expression** — use `reduction = "harmony"`
  for the graph/UMAP/tSNE; run DE on the (joined) RNA expression, never on
  harmony coordinates.
- **Integrate the nuisance, not the signal** — never set `group.by.vars` to the
  biological variable you want to test.
- **Always inspect convergence + mixing** — `plot_convergence = TRUE` should
  flatten; the by-batch UMAP should mix without collapsing distinct cell types.
- **`dims` must be consistent** — the same PC count flows through `RunHarmony`,
  `FindNeighbors`, and `RunUMAP`.
- **v5 split layers** — after `merge()`, `JoinLayers` is required before
  `FindAllMarkers`/`FindMarkers` (see seurat-scrna).

## Python alternative + cross-links
- **scanpy / harmonypy:** in a Python session, one call integrates in place —
  `sc.external.pp.harmony_integrate(adata, key = "batch")` (backed by
  **harmonypy**), which writes `adata.obsm["X_pca_harmony"]`; then
  `sc.pp.neighbors(adata, use_rep = "X_pca_harmony")` → `sc.tl.leiden` /
  `sc.tl.umap`. The full load→concat→QC→PCA→integrate→cluster Python flow (with
  before/after mixing plots) is **harmony-integration-scanpy**; the standalone
  embedding-only step (PCA already computed) is **create-harmony-embeddings-scrna**.
- **scrna-qc-clustering** — the scanpy single-sample QC→clustering baseline (run
  it per sample / pre-integration to confirm a batch effect exists).
- **seurat-scrna** — the R/Seurat single-sample QC→clustering→annotation workflow
  Harmony slots into; consult it for markers/DE and the `JoinLayers` idiom.
- **scvi-integration** — deep-generative (scVI) batch integration; prefer it over
  Harmony for very large atlases, complex/nested batch structure, or when a
  trained model is needed for label transfer (scANVI) or scVI-based DE.

## In ABA
`ensure_capability("Seurat")` and `ensure_capability("harmony")`, then run every
step in `run_r`; `saveRDS` the integrated object so a later `run_r` resumes from
it. Prefer R/Seurat + Harmony when the session is R-based or the user names
Seurat/Harmony; for a Python-native session use harmonypy
(`sc.external.pp.harmony_integrate`) or, for a deeper model, **scvi-integration**.
