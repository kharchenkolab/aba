---
name: seurat-scrna
description: Authoritative single-cell RNA-seq QC + clustering + cluster annotation with R/Seurat (v5) — the standard PBMC3k workflow with current layer idioms, plus the SCTransform alternative.
when_to_use: scRNA-seq dataset (10x CellRanger output or a counts matrix) and you want QC, clustering, a UMAP, and cluster markers / annotation. Use THIS (R/Seurat) when the session is already R-based, when the user asks for Seurat/R, or when downstream work depends on the Bioconductor/Seurat ecosystem. For a Python session (anndata/scanpy already in play), prefer the scanpy equivalent scrna-qc-clustering.
requires_tools: [run_r]
capabilities_needed: [Seurat]
keywords: [Seurat, single cell, scRNA-seq, PBMC3k, clustering, UMAP, marker genes, FindAllMarkers, cell annotation, QC, percent.mt, SCTransform, JoinLayers, R, v5]
produces: [qc_summary.csv, qc_violin.png, umap.png, cluster_markers.csv, annotated_umap.png]
domain: genomics
source: "Seurat PBMC3k guided clustering tutorial + Seurat v5 vignettes (Satija Lab) — satijalab.org/seurat/articles/pbmc3k_tutorial.html"
---

# scRNA-seq QC + clustering + annotation with R/Seurat (v5)

Seurat is the reference R toolkit for single-cell RNA-seq. This is the standard
PBMC3k guided-clustering workflow, written with current **Seurat v5** idioms.
Prefer it over scanpy when the session is already R, the user asks for Seurat/R,
or downstream tools are Bioconductor.

**Provision:** `ensure_capability("Seurat")` (R package — heavy on first install,
cached after; it lives in ABA's curated R base), then `library(Seurat)` in
`run_r`. `library(dplyr)` is also handy for marker post-processing.

## The three choices that DEFINE the result — surface them with present_plan
Halt and walk the user through these before committing; the defaults below are
sensible PBMC3k values but are dataset-dependent. This is exactly where an
advisor adds value.
1. **QC thresholds** — the `subset()` cutoffs on `nFeature_RNA` and `percent.mt`.
   They decide which cells are real vs. debris/doublets.
2. **Number of PCs (dims)** — how many principal components feed the graph and
   UMAP. Read it off the `ElbowPlot`.
3. **Clustering resolution** — `FindClusters(resolution = ...)` controls how many
   clusters you get.

## Input + object creation
`Read10X` reads a CellRanger `filtered_feature_bc_matrix` directory (the
`barcodes.tsv`/`features.tsv`/`matrix.mtx` triple). `CreateSeuratObject` does the
first, gene/cell-level filter: `min.cells` drops genes seen in too few cells,
`min.features` drops near-empty cells.
```r
library(Seurat)
library(dplyr)
pbmc.data <- Read10X(data.dir = file.path(Sys.getenv("DATA_DIR"), "filtered_gene_bc_matrices/hg19"))
pbmc <- CreateSeuratObject(counts = pbmc.data, project = "pbmc3k",
                           min.cells = 3, min.features = 200)
```

## QC metrics + thresholds
Compute mitochondrial fraction with `PercentageFeatureSet` — `pattern = "^MT-"`
matches human MT genes (use `"^mt-"` for mouse). Inspect the distributions, then
`subset`. The tutorial's PBMC3k cutoffs: `nFeature_RNA` 200–2500, `percent.mt < 5`.
```r
pbmc[["percent.mt"]] <- PercentageFeatureSet(pbmc, pattern = "^MT-")
VlnPlot(pbmc, features = c("nFeature_RNA", "nCount_RNA", "percent.mt"), ncol = 3)
FeatureScatter(pbmc, feature1 = "nCount_RNA", feature2 = "percent.mt")
FeatureScatter(pbmc, feature1 = "nCount_RNA", feature2 = "nFeature_RNA")
pbmc <- subset(pbmc, subset = nFeature_RNA > 200 & nFeature_RNA < 2500 & percent.mt < 5)
```

## Normalize → variable features → scale
```r
pbmc <- NormalizeData(pbmc, normalization.method = "LogNormalize", scale.factor = 10000)
pbmc <- FindVariableFeatures(pbmc, selection.method = "vst", nfeatures = 2000)
all.genes <- rownames(pbmc)
pbmc <- ScaleData(pbmc, features = all.genes)   # default scales only variable features; pass all.genes for full matrix
```

## PCA → choose dims → graph → cluster → UMAP
`ElbowPlot` shows where added PCs stop contributing — the tutorial picks **10**
dims for PBMC3k. The same `dims` go to `FindNeighbors` and `RunUMAP`.
```r
pbmc <- RunPCA(pbmc, features = VariableFeatures(object = pbmc))
ElbowPlot(pbmc)                                  # eyeball the elbow → pick dims
pbmc <- FindNeighbors(pbmc, dims = 1:10)
pbmc <- FindClusters(pbmc, resolution = 0.5)     # cluster labels land in Idents(pbmc)
pbmc <- RunUMAP(pbmc, dims = 1:10)
DimPlot(pbmc, reduction = "umap", label = TRUE)
```

## Cluster markers
`FindAllMarkers` runs a one-vs-rest test (default Wilcoxon) per cluster.
`only.pos = TRUE` keeps up-markers; filter on effect size afterward.
```r
pbmc.markers <- FindAllMarkers(pbmc, only.pos = TRUE, min.pct = 0.25, logfc.threshold = 0.25)
top_markers <- pbmc.markers %>% group_by(cluster) %>% dplyr::filter(avg_log2FC > 1)
```

## Annotate clusters from canonical markers
PBMC3k clusters map to known cell types via canonical markers. Re-label with
`RenameIdents` once you've matched markers to biology:
| Cluster | Markers        | Cell type      |
|---------|----------------|----------------|
| 0       | IL7R, CCR7     | Naive CD4 T    |
| 1       | CD14, LYZ      | CD14+ Mono     |
| 2       | IL7R, S100A4   | Memory CD4 T   |
| 3       | MS4A1          | B              |
| 4       | CD8A           | CD8 T          |
| 5       | FCGR3A, MS4A7  | FCGR3A+ Mono   |
| 6       | GNLY, NKG7     | NK             |
| 7       | FCER1A, CST3   | DC             |
| 8       | PPBP           | Platelet       |
```r
new.ids <- c("Naive CD4 T","CD14+ Mono","Memory CD4 T","B","CD8 T",
             "FCGR3A+ Mono","NK","DC","Platelet")
names(new.ids) <- levels(pbmc)
pbmc <- RenameIdents(pbmc, new.ids)
DimPlot(pbmc, reduction = "umap", label = TRUE, pt.size = 0.5)
```

## v5 layers + JoinLayers (read this for multi-sample / DE)
Seurat v5 stores an assay as **layers**: `counts` (raw), `data` (normalized,
written by `NormalizeData`), `scale.data` (written by `ScaleData`). For a single
sample loaded as above, the counts sit in one `counts` layer and **no JoinLayers
is needed** — the workflow above just works.

When you load **multiple samples** (or after integration), v5 keeps the counts
**split** into per-sample layers (`counts.1`, `counts.2`, …). `FindMarkers` /
`FindAllMarkers` then need a single joined matrix:
```r
pbmc[["RNA"]] <- JoinLayers(pbmc[["RNA"]])   # before FindMarkers/FindAllMarkers on split layers
```
Targeted two-group DE uses `FindMarkers`:
```r
FindMarkers(pbmc, ident.1 = "CD8 T", ident.2 = "Naive CD4 T",
            min.pct = 0.25, logfc.threshold = 0.25, test.use = "wilcox")
```

## Alternative — SCTransform (v2)
`SCTransform` is a single command that **replaces `NormalizeData`,
`FindVariableFeatures`, and `ScaleData`**, with built-in variance stabilization
and optional confounder regression. Use it instead of the three-step block above;
the rest of the pipeline is unchanged (note v5 examples use `dims = 1:30` here):
```r
pbmc <- PercentageFeatureSet(pbmc, pattern = "^MT-", col.name = "percent.mt")
pbmc <- SCTransform(pbmc, vars.to.regress = "percent.mt", verbose = FALSE)
pbmc <- RunPCA(pbmc, verbose = FALSE)
pbmc <- FindNeighbors(pbmc, dims = 1:30, verbose = FALSE)
pbmc <- FindClusters(pbmc, verbose = FALSE)
pbmc <- RunUMAP(pbmc, dims = 1:30, verbose = FALSE)
```

## Outputs
```r
write.csv(pbmc@meta.data[, c("nFeature_RNA","nCount_RNA","percent.mt")],
          file.path(Sys.getenv("DATA_DIR"), "qc_summary.csv"))
write.csv(pbmc.markers, file.path(Sys.getenv("DATA_DIR"), "cluster_markers.csv"), row.names = FALSE)
ggplot2::ggsave(file.path(Sys.getenv("ARTIFACTS_DIR"), "umap.png"),
                DimPlot(pbmc, reduction = "umap", label = TRUE))
saveRDS(pbmc, file.path(Sys.getenv("DATA_DIR"), "pbmc.rds"))   # the object, for resuming
```

## Caveats to surface
- **QC cutoffs are dataset-specific** — PBMC3k uses `percent.mt < 5` and
  `nFeature_RNA < 2500`; fresh tissue tolerates higher MT, frozen lower. Don't
  copy 5% blindly. Confirm with the user.
- **`pattern = "^MT-"` is human** — mouse mito genes are `^mt-`.
- **dims is a judgement call** — too few PCs underclusters; the ElbowPlot is a
  guide, not a hard answer.
- **resolution drives cluster count** — 0.5 is moderate; 0.2–0.3 coarser,
  0.8–1.2 finer. Over-clustering invents spurious cell types.
- **JoinLayers** — required before DE only when counts are split across layers
  (multiple samples / post-integration), not for a single sample.

## In ABA
`ensure_capability("Seurat")`, then run every step in `run_r`; persist the object
with `saveRDS` so a later `run_r` can resume from it. If the session is Python
(scanpy/anndata already loaded) or the user wants Python, use the
**`scrna-qc-clustering`** (scanpy) recipe instead — same biology, same steps
(QC → normalize → HVG → scale → PCA → neighbors → cluster → UMAP → markers),
just the anndata/Leiden idioms. Prefer R/Seurat when the session is R-based or
the user names Seurat; prefer Python/scanpy for large datasets, anndata interop,
or a Python-native session.
