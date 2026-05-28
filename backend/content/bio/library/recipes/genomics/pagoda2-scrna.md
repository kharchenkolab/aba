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
1. Load `library(Matrix); library(igraph); library(pagoda2)`. Read counts
   (e.g. `read10xMatrix(path, version='V3')` for CellRanger output) into a
   genes x cells sparse matrix.
2. QC filter: `counts <- gene.vs.molecule.cell.filter(cm, min.cell.size=500)`,
   then drop near-empty genes, e.g. `counts <- counts[rowSums(counts)>=10, ]`.
3. Gene names must be unique: `rownames(counts) <- make.unique(rownames(counts))`.
4. Build the object: `r <- Pagoda2$new(counts, log.scale=TRUE, n.cores=1)`
   (key args: `min.cells.per.gene`, `min.transcripts.per.cell=10`,
   `modelType='plain'`, `batch=` for batch correction).
5. `r$adjustVariance(plot=TRUE, gam.k=10)` — variance normalization /
   overdispersed-gene selection.
6. `r$calculatePcaReduction(nPcs=50, n.odgenes=3e3)` — PCA on OD genes,
   stored under name `'PCA'`.
7. `r$makeKnnGraph(k=40, type='PCA', center=TRUE, distance='cosine')`.
8. `r$getKnnClusters(method=infomap.community, type='PCA')` — community
   detection. Pass any igraph method: `multilevel.community` (Louvain, the
   `basicP2proc` default), `walktrap.community`, `infomap.community`. Use
   `name=` to store several side by side (default name is `'community'`).
9. Embed: `r$getEmbedding(type='PCA', embeddingType='largeVis', M=30, perplexity=30, gamma=1/30)`
   or `embeddingType='tSNE'`. Visualize with
   `r$plotEmbedding(type='PCA', embeddingType='tSNE', mark.groups=TRUE, ...)`;
   overlay a gene with `colors=r$counts[,gene]`.
10. DE: `r$getDifferentialGenes(type='PCA', clusterType='community', verbose=TRUE)`.
    Results land in `r$diffgenes$PCA[[1]][['<cluster>']]`. Heatmap via
    `r$plotGeneHeatmap(genes=rownames(de)[1:15], groups=r$clusters$PCA[[1]])`.
11. Persist: `saveRDS(r, 'pagoda2object.rds')`.

Fast path: `p2 <- basicP2proc(cd, n.cores=1, min.cells.per.gene=10, n.odgenes=2e3, get.largevis=FALSE, make.geneknn=FALSE)`
runs steps 3-9 (multilevel clustering + largeVis/tSNE) in one call. It does
`make.unique` internally.

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
- Matrix orientation is genes x cells — the constructor errors on duplicate
  or NA gene/cell names; sanitize first.
- Methods mutate the object in place; do not reassign the return value.
- `testPathwayOverdispersion` is slow on >1k cells — prefer hierarchical DE.
- largeVis is much faster than tSNE; tSNE perplexity auto-shrinks if too
  large for the cell count.

## In ABA
- One step: `ensure_capability("pagoda2")` — it's a known capability in the
  catalog (GitHub R package) and installs into the project R library. Then run
  every step in `run_r`. **Don't hand-roll `install_github` and don't
  `ensure_capability` Matrix/igraph/irlba separately** — those (plus xml2 and
  igraph's GLPK system lib) already ship with the R runtime as conda binaries,
  so `library(Matrix); library(igraph)` just work. For example data the vignette
  uses the `p2data` drat package, but real inputs come from `read10xMatrix` or a
  saved matrix.
