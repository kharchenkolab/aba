---
name: seurat-integration
description: Integrate multiple scRNA-seq samples with R/Seurat v5 — build ONE merged object with per-sample layers (split by sample), preprocess (Normalize→HVG→Scale→PCA), then IntegrateLayers (CCAIntegration / RPCAIntegration / HarmonyIntegration) into a new reduction, cluster/UMAP on the integrated reduction, and JoinLayers before DE. The modern v5 layer-based replacement for the classic FindIntegrationAnchors/IntegrateData anchor flow.
when_to_use: Two or more scRNA-seq samples/donors/conditions/batches (10x lanes, stim vs ctrl, multiple GEO GSMs) whose batch effect is visible in a plain PCA/UMAP, and you want to integrate them into one shared embedding before clustering/annotation, using R/Seurat. Use THIS (R/Seurat v5) when the session is R-based or the user asks for Seurat integration / anchors / IntegrateLayers / CCA / RPCA. For just Harmony in Seurat see harmony-integration; for a deep-generative method see scvi-integration; for a Python/scanpy session use the scanpy integration recipes. A single clean sample needs no integration — see seurat-scrna / scrna-qc-clustering.
requires_tools: [run_r]
capabilities_needed: [Seurat]
keywords: [Seurat integration, Seurat v5, anchor, anchors, FindIntegrationAnchors, IntegrateData, IntegrateLayers, CCAIntegration, RPCAIntegration, HarmonyIntegration, FastMNNIntegration, batch correction, batch effect, sample integration, multi-sample, multiple samples, integrate samples, scRNA-seq, single cell, JoinLayers, split layers, per-sample layers, integrated UMAP, integrated.cca, integrated.rpca, reduction, FindClusters, RunUMAP, merge, ReadMtx, GEO, R]
produces: [umap_unintegrated_by_sample.png, umap_integrated_by_sample.png, umap_integrated_by_cluster.png, integrated.rds]
domain: genomics
source: "Seurat v5 integration vignette (Satija Lab) — satijalab.org/seurat/articles/seurat5_integration.html"
---

# scRNA-seq integration of multiple samples with R/Seurat (v5)

Integration aligns several scRNA-seq samples into **one shared low-dimensional
embedding** so that the same cell types from different samples co-embed, while
distinct cell types stay apart — letting you cluster and annotate across the
whole set instead of per-sample. This is the **modern Seurat v5** way, built on
**layers**: you keep a single object whose counts are *split into one layer per
sample*, preprocess it like a normal sample, and then `IntegrateLayers` learns a
batch-corrected reduction. It replaces the classic v4
`FindIntegrationAnchors`/`IntegrateData` anchor pipeline (documented at the end
as the alternative).

**Provision:** `ensure_capability("Seurat")` (CRAN R package, installed on demand
into the project R library — heavy the first time, mostly PPM *binaries* so a few
minutes, then cached), then in `run_r`:
```r
library(Seurat)
library(ggplot2)   # ggtitle()/aes() on DimPlots — library(Seurat) does NOT attach ggplot2
```
**`library(Seurat)` does NOT attach ggplot2** — load it yourself or every
`+ ggtitle("...")` on a DimPlot errors with `could not find function "ggtitle"`.
(There is no `tidyverse` meta-package here — never `library(tidyverse)`.)

## The choices that DEFINE the integration — surface them with present_plan
Halt and walk the user through these before committing; this is exactly where an
advisor adds value, because over-integration silently erases real biology.
1. **What to integrate over (the split variable)** — the per-sample/batch
   covariate you split the layers on. Split on the **technical** nuisance
   (sample, donor, lane, batch, 10x run), **never** on the biological variable
   you intend to test. If `stim`/`ctrl` IS the question, integrating over it
   washes out the very signal you're studying.
2. **Method** — `CCAIntegration` (default; good when samples share most cell
   types, can over-correct rare/sample-specific populations), `RPCAIntegration`
   (faster, more conservative — preferred for large datasets or when populations
   are only partly shared), or `HarmonyIntegration` (fast linear correction). Pick
   one; you can re-run `IntegrateLayers` with another method into a *different*
   `new.reduction` to compare.
3. **Number of PCs (`dims`)** — how many dimensions feed the integration and the
   downstream graph/UMAP (the vignette uses `1:30`). Read the elbow off
   `ElbowPlot(obj)`.
4. **Clustering resolution** — `FindClusters(resolution = ...)` sets cluster
   count, as in any Seurat run.

## 1. Load each sample → merge → split into per-sample layers
Build ONE object spanning all samples, tag the batch covariate in `meta.data`,
then **split the RNA assay** so v5 keeps one layer per sample.

`Read10X` reads a CellRanger `filtered_feature_bc_matrix` **directory** with the
standard `barcodes.tsv`/`features.tsv`/`matrix.mtx` names. **GEO supplementary
files are usually loose and GSM-prefixed** (e.g.
`GSM5746268_..._matrix.mtx.gz` + `..._barcodes.tsv.gz` + `..._features.tsv.gz`
all sitting in one directory) — `Read10X` will NOT find these (non-standard
names), so use **`ReadMtx` with explicit paths**. `ReadMtx` also de-dups gene
symbols, which matters because `CreateSeuratObject` errors on duplicate gene
rownames. Files live under `Sys.getenv("DATA_DIR")`.
```r
D <- Sys.getenv("DATA_DIR")

# Per-sample GSM-prefixed triplets (gz ok). One ReadMtx per sample, explicit paths;
# feature.column = 2 → gene symbols (col 1 = Ensembl). Add yours here:
samples <- list(
  ctrl = c(mtx = "GSM5746268_..._matrix.mtx.gz",
           cells = "GSM5746268_..._barcodes.tsv.gz",
           features = "GSM5746268_..._features.tsv.gz"),
  stim = c(mtx = "GSM5746269_..._matrix.mtx.gz",
           cells = "GSM5746269_..._barcodes.tsv.gz",
           features = "GSM5746269_..._features.tsv.gz")
)
objs <- lapply(names(samples), function(s) {
  f <- samples[[s]]
  m <- ReadMtx(mtx = file.path(D, f["mtx"]), cells = file.path(D, f["cells"]),
               features = file.path(D, f["features"]), feature.column = 2)
  CreateSeuratObject(counts = m, project = s, min.cells = 3, min.features = 200)
})
# Standard CellRanger dir per sample instead:
#   CreateSeuratObject(Read10X(file.path(D, "<sample_dir>")), project = s, min.cells = 3)

# Merge into one object (add.cell.ids keeps barcodes unique) and label the batch covariate
obj <- merge(objs[[1]], y = objs[-1], add.cell.ids = names(samples))
obj$sample <- obj$orig.ident          # the technical covariate to integrate over

# v5: split the RNA assay into one layer per sample — THIS is what integration aligns
obj[["RNA"]] <- split(obj[["RNA"]], f = obj$sample)
```
After `split()`, the assay holds per-sample `counts.<sample>` (and, after
preprocessing, `data.<sample>` / `scale.data.<sample>`) layers — that split is
exactly what `IntegrateLayers` reads.

## 2. Preprocess the merged object → PCA (exactly like a single sample)
With layers split, the standard four steps run **per layer automatically** — no
per-sample loop needed. `ScaleData` MUST run **before** `RunPCA`.
```r
# (optional QC, as for one sample — compute %mito and subset before normalizing)
# obj[["percent.mt"]] <- PercentageFeatureSet(obj, pattern = "^MT-")   # "^mt-" for mouse
# obj <- subset(obj, subset = nFeature_RNA > 200 & nFeature_RNA < 2500 & percent.mt < 5)

obj <- NormalizeData(obj)
obj <- FindVariableFeatures(obj)
obj <- ScaleData(obj)
obj <- RunPCA(obj)
```

## 3. Pre-integration UMAP — show the batch effect FIRST
Run a UMAP on the raw `pca` reduction and colour it by sample. If the samples
form separate islands per cell type, there's a batch effect worth integrating.
Keep this reduction under its own name (`umap.unintegrated`) so it doesn't
clobber the integrated one.
```r
obj <- RunUMAP(obj, reduction = "pca", dims = 1:30, reduction.name = "umap.unintegrated")
DimPlot(obj, reduction = "umap.unintegrated", group.by = "sample") +
  ggtitle("Before integration — colored by sample (batch effect)")   # KEY FIGURE 1
```

## 4. IntegrateLayers — the integration step (verified v5 API)
One call learns a batch-corrected reduction from the split layers. Pass the
method as a **function name (unquoted)**, the source reduction in
`orig.reduction`, and a name for the result in `new.reduction`.
```r
# DEFAULT — CCA:
obj <- IntegrateLayers(
  object = obj, method = CCAIntegration,
  orig.reduction = "pca", new.reduction = "integrated.cca",
  verbose = FALSE)

# Alternatives (same call, swap method + new.reduction):
#   method = RPCAIntegration,    new.reduction = "integrated.rpca"   # faster, conservative
#   method = HarmonyIntegration, new.reduction = "harmony"           # fast linear (see harmony-integration)
#   method = FastMNNIntegration, new.reduction = "integrated.mnn"    # (no orig.reduction needed)
```
The result is a new reduction (here `"integrated.cca"`) holding the corrected
embedding — this is what every downstream step uses instead of `"pca"`.

## 5. Downstream — cluster + UMAP on the INTEGRATED reduction
The only change vs an unintegrated run: point `FindNeighbors` and `RunUMAP` at
`reduction = "integrated.cca"`. Give the UMAP its own `reduction.name` so it sits
alongside the pre-integration one.
```r
obj <- FindNeighbors(obj, reduction = "integrated.cca", dims = 1:30)
obj <- FindClusters(obj, resolution = 0.5, cluster.name = "integrated_clusters")
obj <- RunUMAP(obj, reduction = "integrated.cca", dims = 1:30, reduction.name = "umap.cca")
```

> **Leiden vs Louvain.** `FindClusters(algorithm = 4)` is Leiden, but it needs
> `leidenalg` configured via reticulate; if it errors (e.g.
> `cannot find Leiden algorithm` / a Python/`leidenalg` import failure), fall
> back to the **default Louvain** (`algorithm = 1`) — just drop the `algorithm`
> argument as shown above. Louvain is the Seurat default and is fine for a first pass.

## 6. Assess mixing — did it work?
Integration succeeds when cells from different samples **interleave** within
shared cell types yet **distinct cell types stay separate**. The post-integration
UMAP coloured by sample should be well-mixed (contrast it with the
pre-integration one from step 3); the same UMAP coloured by cluster should show
clean populations.
```r
DimPlot(obj, reduction = "umap.cca", group.by = "sample") +
  ggtitle("After integration — colored by sample (mixed)")           # KEY FIGURE 2
DimPlot(obj, reduction = "umap.cca", group.by = "integrated_clusters", label = TRUE) +
  ggtitle("After integration — clusters")                            # KEY FIGURE 3
```
Read it: if samples still form separate islands per cell type, integration is too
weak (try `RPCAIntegration` → CCA, or raise `dims`); if biologically distinct
types collapsed together, it's too aggressive.

## 7. JoinLayers — required before any DE
Integration leaves the counts split across per-sample layers. Before
`FindMarkers`/`FindAllMarkers`, **rejoin** them into a single matrix.
```r
obj <- JoinLayers(obj)                              # or obj[["RNA"]] <- JoinLayers(obj[["RNA"]])
# markers/DE then follow seurat-scrna (FindAllMarkers / FindMarkers on the joined RNA expression)
```
DE runs on the **joined RNA expression**, never on the integrated embedding (the
embedding is for neighbors/UMAP only). For scRNA-seq DE use Seurat's
`FindMarkers`/`FindAllMarkers` (Wilcoxon) — not bulk tools like DESeq2 on
per-cell counts.

## Outputs
~3 key figures (capped) + the integrated object. ggplots auto-register as figures
when printed; `ggsave` writes explicitly into the artifacts dir.
```r
ggsave(file.path(Sys.getenv("ARTIFACTS_DIR"), "umap_unintegrated_by_sample.png"),
       DimPlot(obj, reduction = "umap.unintegrated", group.by = "sample") +
         ggtitle("Before integration — by sample"), width = 6, height = 5)
ggsave(file.path(Sys.getenv("ARTIFACTS_DIR"), "umap_integrated_by_sample.png"),
       DimPlot(obj, reduction = "umap.cca", group.by = "sample") +
         ggtitle("After integration — by sample"), width = 6, height = 5)
ggsave(file.path(Sys.getenv("ARTIFACTS_DIR"), "umap_integrated_by_cluster.png"),
       DimPlot(obj, reduction = "umap.cca", group.by = "integrated_clusters", label = TRUE) +
         ggtitle("After integration — clusters"), width = 6, height = 5)
saveRDS(obj, file.path(Sys.getenv("DATA_DIR"), "integrated.rds"))   # resume later
```

## Honor the requested scope
Integrate **only what's asked**. Do the integration, the three diagnostic UMAPs,
and stop. Do **not** tack on unsolicited cluster annotation, cell-type calling,
marker tables, or written summaries/reports — those are separate, explicit
requests (markers/DE → seurat-scrna once asked). Keep the plan about integrating
the named samples, nothing more.

## Caveats to surface
- **Split on the nuisance, not the signal** — never split layers on the
  biological variable you intend to test; you'd integrate the effect away.
- **`library(ggplot2)` is required** for `ggtitle()`/`aes()` — Seurat returns
  ggplot objects but does not attach ggplot2.
- **GEO loose/GSM-prefixed files need `ReadMtx`**, not `Read10X` (which needs a
  standard dir); `ReadMtx` de-dups gene symbols, avoiding the duplicate-rowname error.
- **`dims` must be consistent** across `RunUMAP`, `IntegrateLayers` downstream,
  `FindNeighbors`, and the integrated `RunUMAP`.
- **JoinLayers before DE** — counts stay split per sample after integration;
  rejoin before `FindMarkers`/`FindAllMarkers`.
- **Leiden needs leidenalg** — `algorithm = 4` requires a configured Python
  `leidenalg`; on error fall back to Louvain (`algorithm = 1`, the default).
- **Over-integration erases biology** — always compare the pre- vs
  post-integration by-sample UMAP; mixing should not collapse distinct cell types.

## Alternative — classic v4 anchor pipeline (FindIntegrationAnchors / IntegrateData)
The pre-v5 route uses a **list of separate objects**, finds anchors across them,
and materializes a new `integrated` assay. Prefer the v5 `IntegrateLayers` flow
above; use this only if you must reproduce a v4 analysis.
```r
obj.list <- SplitObject(obj, split.by = "sample")     # list of per-sample objects
obj.list <- lapply(obj.list, function(x) {
  x <- NormalizeData(x); FindVariableFeatures(x, selection.method = "vst", nfeatures = 2000)
})
features <- SelectIntegrationFeatures(obj.list)
anchors  <- FindIntegrationAnchors(object.list = obj.list, anchor.features = features,
                                   reduction = "cca")   # 'cca'/'rpca'/'jpca'/'rlsi' — NEVER 'pca'
combined <- IntegrateData(anchorset = anchors)
DefaultAssay(combined) <- "integrated"
combined <- ScaleData(combined)                         # MUST ScaleData BEFORE RunPCA on the
combined <- RunPCA(combined)                            #   integrated assay (else "no 'dimnames' attribute")
combined <- FindNeighbors(combined, dims = 1:30) |> FindClusters(resolution = 0.5)
combined <- RunUMAP(combined, dims = 1:30)
```
- **`reduction` for `FindIntegrationAnchors` must be `'cca'`, `'rpca'`, `'jpca'`,
  or `'rlsi'` — never `'pca'`** (that's not a valid anchor reduction).
- On the `integrated` assay you **must `ScaleData` before `RunPCA`**, or `RunPCA`
  fails with `no 'dimnames' attribute for array`.
- DE in the v4 flow runs on the **`RNA`** assay (`DefaultAssay(combined) <- "RNA"`),
  not the `integrated` assay.

## Cross-links
- **harmony-integration** — Harmony in Seurat (also reachable here via
  `method = HarmonyIntegration`); fast linear batch correction of an embedding.
- **scvi-integration** — deep-generative (scVI) integration; prefer for very large
  atlases, complex/nested batch structure, or label transfer (scANVI).
- **seurat-scrna** — single-sample QC→clustering→annotation; consult it for the
  markers/DE step (`FindAllMarkers`/`FindMarkers`) after `JoinLayers`.
- **scrna-qc-clustering** — scanpy single-sample baseline (run per sample /
  pre-integration to confirm a batch effect exists).
- Python/scanpy session → use the scanpy integration recipes
  (`harmony-integration` covers harmonypy via `sc.external.pp.harmony_integrate`).

## In ABA
`ensure_capability("Seurat")`, then run every step in `run_r`; `saveRDS` the
integrated object so a later `run_r` resumes from it. Prefer R/Seurat v5
`IntegrateLayers` when the session is R-based or the user names Seurat
integration / anchors / CCA / RPCA; for a Python-native session use the scanpy
integration recipes.
