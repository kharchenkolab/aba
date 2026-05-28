---
name: conos-integration
description: Joint/integrative analysis of multiple scRNA-seq samples with the conos R6 pipeline (joint graph, clustering, label propagation, DE)
when_to_use: Two or more scRNA-seq samples to integrate into one joint graph; want cross-sample clusters, a shared embedding, label transfer, or between-group differential expression
requires_tools: [run_r]
capabilities_needed: [conos, pagoda2, Matrix, igraph]
keywords: [conos, integration, batch correction, multi-sample, joint graph, mNN, label propagation, leiden, scRNA-seq, kharchenkolab, R6]
produces: [Conos object, joint clusters, joint 2D embedding, propagated labels, per-cluster markers, between-group DE tables]
domain: genomics
source: github:kharchenkolab/conos + vignette https://github.com/kharchenkolab/conos/blob/main/doc/walkthrough.md
---
# conos multi-sample integration

conos is an R6 package for joint analysis of a panel of single-cell
samples. It does NOT preprocess raw counts itself — each sample must first
be a preprocessed `Pagoda2` (or `Seurat`) object. The `Conos` object holds
the list of samples and builds a joint kNN graph across them, on which you
cluster, embed, propagate labels, and run DE. Methods mutate the object in
place via `$`.

## Approach
1. `library(conos)`. Start from a **named list** of count matrices (genes x
   cells), one per sample. Ensure cell names are globally unique across
   samples (prefix by sample id) — `any(duplicated(unlist(lapply(panel, colnames))))`
   must be FALSE.
2. Preprocess each sample. With pagoda2:
   `panel.preprocessed <- lapply(panel, basicP2proc, n.cores=1, min.cells.per.gene=0, n.odgenes=2e3, get.largevis=FALSE, make.geneknn=FALSE)`.
   `min.cells.per.gene=0` keeps genes for fair cross-sample comparison. With
   Seurat use `lapply(panel, basicSeuratProc)`, or pass existing Seurat
   objects directly.
3. `con <- Conos$new(panel.preprocessed, n.cores=1)`.
4. Build the joint graph:
   `con$buildGraph(k=30, k.self=5, space='PCA', ncomps=30, n.odgenes=2000, matching.method='mNN', metric='angular', score.component.variance=TRUE)`.
   `space=` may be 'PCA' (fast default), 'CPCA' (more distortion-robust),
   'CCA' (low-similarity / cross-species), or 'genes' (same platform).
   To recompute a space, clear its cache: `con$pairs$PCA <- NULL`.
5. Joint clustering: `con$findCommunities(method=leiden.community, resolution=1)`.
   Other methods: `igraph::walktrap.community` (hierarchical, use `steps=8-10`),
   `igraph::multilevel.community`. Results stored as a list under
   `con$clusters$<name>` (e.g. `con$clusters$leiden$groups`).
6. Embed the joint graph: `con$embedGraph(method='largeVis')` (default) or
   `con$embedGraph(method='UMAP', min.dist=0.01, spread=15)`. Since conos
   >1.3.1 you MUST call `embedGraph` explicitly before plotting. Name
   multiple embeddings with `embedding.name=`.
7. Visualize: `con$plotPanel(clustering='leiden', font.size=4)` (per-sample
   small multiples; `use.local.clusters=TRUE` shows each sample's own
   clusters) and `con$plotGraph(color.by='sample', alpha=0.1)` /
   `con$plotGraph(gene='GZMK')` (the joint embedding). Both wrap
   `sccore::embeddingPlot`.
8. Label propagation: `info <- con$propagateLabels(labels=cellannot, verbose=TRUE)`
   transfers a named factor of annotations from labeled to unlabeled cells.
   Returns `$labels`, `$uncertainty`, `$label.distribution`.
9. Cluster markers: `de <- con$getDifferentialGenes(groups=new.annot, append.auc=TRUE)`
   — per-group table with M, Z, PValue, PAdj, AUC, Specificity, Precision.
   Heatmap: `plotDEheatmap(con, as.factor(groups), de, n.genes.per.cluster=5, column.metadata=list(samples=con$getDatasetPerCell()))`.
10. Between-group DE: define `samplegroups <- list(bm=c(...), cb=c(...))`, then
    `getPerCellTypeDE(con, groups=as.factor(new.annot), sample.groups=samplegroups, ref.level='bm')`
    (pseudobulk per cluster, DESeq2 under the hood). For custom models pull
    meta-cell counts with `con$getClusterCountMatrices()`.

## Key decisions / parameters
- `space`: PCA is the fast default; escalate to CPCA/CCA only when samples
  are highly dissimilar. With 'angular' metric keep ~30 components (don't cut
  to the variance elbow); with 'L2' fewer components can be better.
- `findCommunities` `resolution` (leiden) trades cluster granularity;
  walktrap `steps` higher = finer + slower.
- Force tighter alignment with `buildGraph(alignment.strength=0.3, ...)` and
  `balance.edge.weights=` to rebalance by a factor (e.g. tissue).
- `greedyModularityCut(con$clusters$walktrap$result, N)` cuts the walktrap
  dendrogram to N clusters for hierarchical exploration.

## Caveats
- Inputs are preprocessed per-sample objects, not raw matrices — run
  pagoda2/Seurat first.
- Cell names must be unique across the whole panel or the graph is corrupt.
- Plotting needs an explicit prior `embedGraph` call.
- Joint clusters are comparable across samples; per-sample local clusters
  (`use.local.clusters=TRUE`) are not.

## In ABA
- Install via `ensure_capability` — conos is a GitHub R package:
  propose_capability(archetype='r_package', source='github', package='kharchenkolab/conos').
  It depends on pagoda2 (also a GitHub r_package) for preprocessing, plus
  Matrix and igraph; igraph needs the GLPK system lib (conda `glpk`). Run all
  steps in `run_r`.
