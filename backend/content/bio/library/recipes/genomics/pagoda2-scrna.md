---
name: pagoda2-scrna
description: Single-sample scRNA-seq processing, clustering, embedding and DE with the pagoda2 R6 pipeline
when_to_use: One scRNA-seq sample (sparse count matrix); want fast normalization, variance adjustment, PCA, KNN clustering, 2D embedding, marker DE, and optionally an interactive web app
requires_tools: [run_r]
capabilities_needed: [pagoda2]
keywords: [pagoda2, single cell, scRNA-seq, clustering, embedding, largeVis, tSNE, differential expression, kharchenkolab, R6]
produces: [Pagoda2 object (rds), cluster factors, 2D embedding, differential genes table, optional *.bin web app]
domain: genomics
source: github:kharchenkolab/pagoda2 + vignette https://pklab.med.harvard.edu/peterk/p2/walkthrough.nb.html
---
# pagoda2 single-cell RNA-seq processing

pagoda2 is an R6 package. The analysis object is `Pagoda2`; you mutate it
in place via `$`-methods (no reassignment). The count matrix must be
**genes x cells** (rows = genes, columns = cells), a `dgCMatrix` from the
Matrix package. This is the opposite layout from scanpy/AnnData — transpose
if coming from a cells x genes source.

## Approach
1. Load `library(Matrix); library(igraph); library(pagoda2)`, then read the 10x MTX
   triplet **from explicit file paths** with this small inline reader. It reads `.gz`
   directly (don't gunzip), tolerates GEO's GSM-prefixed filenames (no rename/symlink
   dance), and pins the **genes × cells** orientation so there's no transpose ambiguity:
   ```r
   read_10x_explicit <- function(mtx, barcodes, features, gene.col = 2) {
     op <- function(p) if (grepl("\\.gz$", p)) gzfile(p) else p   # transparent gz
     m  <- as(Matrix::readMM(op(mtx)), "CsparseMatrix")           # genes (rows) x cells (cols)
     ft <- utils::read.delim(op(features), header = FALSE)        # col1=Ensembl, col2=symbol
     # make.unique FIRST: gene symbols often repeat — pagoda2 errors on dup/NA names.
     rownames(m) <- make.unique(as.character(ft[[ if (ncol(ft) >= gene.col) gene.col else 1 ]]))
     colnames(m) <- readLines(op(barcodes))
     m
   }
   cm <- read_10x_explicit(
     file.path(DATA_DIR, "GSM5746268_..._matrix.mtx.gz"),
     file.path(DATA_DIR, "GSM5746268_..._barcodes.tsv.gz"),
     file.path(DATA_DIR, "GSM5746268_..._features.tsv.gz"))
   ```
   `gene.col=2` uses gene symbols; pass `1` for Ensembl IDs. For **multiple samples**,
   read each into its own matrix and integrate with **conos** — don't `cbind` raw counts.
   (pagoda2's `read.10x.matrices(matrixPaths, version='V3')` is the convenience path
   *only* when files already sit in a per-sample dir under standard CellRanger names —
   GEO files usually don't, so prefer the explicit reader above.)
2. QC filter: `counts <- gene.vs.molecule.cell.filter(cm, min.cell.size=500)`,
   then drop near-empty genes, e.g. `counts <- counts[rowSums(counts)>=10, ]`.
3. Gene names must be unique: `rownames(counts) <- make.unique(rownames(counts))`.
4. Build the object: `r <- Pagoda2$new(counts, log.scale=TRUE, n.cores=1)`
   (key args: `min.cells.per.gene`, `min.transcripts.per.cell=10`,
   `modelType='plain'`, `batch=` for batch correction).
5. `r$adjustVariance(plot=TRUE, gam.k=10)` — variance normalization /
   overdispersed-gene selection.
6. `r$calculatePcaReduction(nPcs=50, n.odgenes=3e3)` — PCA on OD genes,
   stored under reduction name `'PCA'`.
7. `r$makeKnnGraph(k=40, type='PCA', center=TRUE, distance='cosine')`.
   - **CRITICAL — `type` must be the PCA reduction (`'PCA'`), NEVER `'counts'`.**
     `type=` selects the space the graph/clustering/embedding operate in. With
     `type='counts'` pagoda2 builds the kNN graph on the **raw ~15k-dimensional
     count matrix** — that doesn't error, it just **HANGS** (the run "times out",
     which looks like a variance/normalization stall but is really the kNN step).
     Steps 7–10 below all pass `type='PCA'` for this reason. (`calculatePcaReduction`
     reads from counts and *stores* under name `'PCA'`; everything downstream reads
     that stored reduction by name.)
8. **Clustering — default to Leiden.** `r$getKnnClusters(method=leidenAlg::leiden.community, type='PCA', name='leiden', resolution=1.0)`.
   `getKnnClusters` takes an igraph-style community function; Leiden comes from the
   `leidenAlg` package — `ensure_capability("leidenAlg")` first (it is NOT in the base
   R runtime; invoking Leiden without it is what raised the "no package called conos" /
   missing-package errors). `resolution` tunes granularity (higher = more clusters).
   Other methods only if asked: `igraph::multilevel.community` (Louvain),
   `walktrap.community`, `infomap.community`. Clusters land in `r$clusters$PCA[[name]]`.
9. **Embedding — default to UMAP.** UMAP is `r$getEmbedding(type='PCA', embeddingType='UMAP', distance='cosine', n.cores=1)`
   — **there is NO `r$calculateUmap()` method; do not guess one.** `getEmbedding` stores
   the result in `r$embeddings$PCA$UMAP`. (UMAP uses `uwot`, which ships with the runtime.) `largeVis`/`tSNE` stay available
   if asked. Visualize with
   `r$plotEmbedding(type='PCA', embeddingType='UMAP', clusterType='leiden', mark.groups=TRUE)`
   — `clusterType=` MUST match the cluster `name=` from step 8 and `embeddingType=` the
   one you computed, or you get "Clustering <x> for type PCA doesn't exist". Overlay a
   gene with `colors=r$counts[,gene]`.
10. **Marker DE — use the built-in, do NOT hand-roll.** `r$getDifferentialGenes(type='PCA', clusterType='leiden', verbose=TRUE, upregulated.only=TRUE)`
    (`clusterType` = your cluster `name=`). Results land in `r$diffgenes$PCA$leiden`.
    Manually slicing `r$counts` by cluster to compute markers is the classic failure
    mode: **`r$counts` is stored cells x genes (the transpose of the input)**, so
    hand-rolled row/col means silently mismatch the cluster factor. Let pagoda2 do it.
11. **Marker heatmap — `plotDEheatmap`** (the preferred marker viz). It is a **conos**
    function — not a pagoda2 method — that also accepts a pagoda2 object as the first
    arg. See the conos walkthrough "Cluster markers" section:
    https://github.com/kharchenkolab/conos/blob/main/doc/walkthrough.md#cluster-markers
    `conos::plotDEheatmap(r, r$clusters$PCA$leiden, r$diffgenes$PCA$leiden, n.genes.per.cluster=10)`.
    Provision: `ensure_capability("conos")` (provides plotDEheatmap) **and**
    `ensure_capability("ComplexHeatmap")` — install ComplexHeatmap as the conda
    Bioconductor *binary*; a source compile is heavy/fragile (see [[r-binary-channels]]).
12. Persist: `saveRDS(r, file.path(DATA_DIR, 'pagoda2object.rds'))` — use the injected
    `DATA_DIR` variable (or the session working dir), NOT bare `/tmp`.

Fast path: `p2 <- basicP2proc(cd, n.cores=1, min.cells.per.gene=10, n.odgenes=2e3, get.largevis=FALSE, make.geneknn=FALSE)`
runs steps 3-9 in one call (Louvain/multilevel clustering + largeVis by default). It
does `make.unique` internally. For the Leiden + UMAP defaults above, either run the
steps explicitly, or call `basicP2proc` then re-run steps 8-9 (Leiden cluster + UMAP).

Web app (optional): `r$makeGeneKnnGraph()`, build genesets (e.g.
`hierDiffToGenesets(r$getHierarchicalDiffExpressionAspects(type='PCA', clusterName='community'))`),
then `p2web <- make.p2.app(r, dendrogramCellGroups=r$clusters$PCA$community, geneSets=genesets, appmetadata=list(apptitle='...'))`
and `p2web$serializeToStaticFast('app.bin')`. Convenience: `basicP2web(p2)`.
GO overdispersion: `ext <- extendedP2proc(p2, organism='hs')` (also 'mm','dr').

## Key decisions / parameters
- `nPcs` (PCA dims) and `n.odgenes` (overdispersed genes) scale with dataset
  complexity; ~50 PCs / 2-3k OD genes is typical.
- `k` in `makeKnnGraph` controls cluster granularity (larger k -> coarser).
- Clustering method changes resolution character: multilevel = balanced
  default, walktrap = finer/hierarchical, infomap = information-theoretic.
- `distance='cosine'` with `center=TRUE` is the standard KNN setup.

## Caveats
- INPUT orientation is genes x cells; the constructor errors on duplicate or NA
  gene/cell names — sanitize first. But pagoda2 stores `r$counts` **internally as
  cells x genes** (transposed from the input) — never assume `r$counts` is genes x
  cells when post-processing; prefer the built-in methods (`getDifferentialGenes`,
  `plotEmbedding`) over hand-slicing the matrix.
- Methods mutate the object in place; do not reassign the return value.
- `testPathwayOverdispersion` is slow on >1k cells — prefer hierarchical DE.
- largeVis is much faster than tSNE; tSNE perplexity auto-shrinks if too
  large for the cell count.

## In ABA
- Provision: `ensure_capability("pagoda2")` (CRAN R package → project R library, installs as a PPM binary — fast).
  For the defaults above also `ensure_capability("leidenAlg")` (Leiden clustering)
  and, for the marker heatmap, `ensure_capability("conos")` + `ensure_capability("ComplexHeatmap")`.
  UMAP's `uwot` already ships with the runtime. **Don't hand-roll `install_github`
  and don't `ensure_capability` Matrix/igraph/irlba separately** — those (plus xml2
  and igraph's GLPK system lib) already ship as conda binaries, so
  `library(Matrix); library(igraph)` just work. **Never** `install.packages("pagoda2",
  repos="https://cloud.r-project.org")` in `run_r` — cloud is source-only and
  source-compiles the whole Rcpp dep tree (slow). Use `ensure_capability`; the run_r
  session's repo already defaults to ABA's PPM binary mirror if you must install ad hoc.
- Write outputs (plots, RDS, CSVs) under the injected `DATA_DIR` / the session working
  dir — NOT bare `/tmp` (it isn't compartmentalized and isn't picked up as a project
  artifact). Better still, let plots render in the cell so run_r captures them as figure
  entities automatically; `register_dataset(...)` any table worth keeping.
- Real inputs come from `read.10x.matrices` or a saved matrix (the vignette's `p2data`
  drat package is example-only).
