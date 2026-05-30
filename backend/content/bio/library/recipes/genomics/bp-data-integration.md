---
name: bp-data-integration
description: Best-practice scRNA-seq data integration / batch correction — scVI & scANVI (top scIB performers), with Harmony/Scanorama/BBKNN alternatives and scIB evaluation, per the Single-cell Best Practices book.
when_to_use: Use this for the integration / batch-correction STAGE only — multiple scRNA-seq batches/samples/donors that need a shared embedding before joint clustering/annotation, choosing a method by whether labels exist and ranking with scIB. For the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, scvi-tools, scanorama, harmonypy, bbknn, scib-metrics]
keywords: [data integration, batch correction, scVI, scANVI, Harmony, Scanorama, BBKNN, FastMNN, scIB evaluation, batch key, latent embedding]
produces: [adata_integrated.h5ad, integration_umap.png, scib_metrics.csv]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/cellular_structure/integration.html"
---

# scRNA-seq data integration (best practice)

Remove batch (technical) variation while preserving biology, so cells of the same type from
different samples co-locate. The book's guidance: **visualize first** (you may not need it),
prefer **label-aware** methods when labels exist, **try several** and rank with **scIB**.

**Provision:** `ensure_capability(["scanpy","anndata","scvi-tools","scanorama","harmonypy","bbknn","scib-metrics"])`
(install only what you'll run).

## First: do you even need it, and what is "batch"?
```python
import scanpy as sc
sc.pp.neighbors(adata); sc.tl.umap(adata)
sc.pl.umap(adata, color="batch")   # if types separate by batch -> integrate; if mixed -> maybe skip
```
Choosing the **batch covariate** is a biology decision: using *sample* as batch removes donor +
location differences; using *donor* preserves location. Don't treat real biology as batch.

## Batch-aware HVGs (before any method)
```python
sc.pp.highly_variable_genes(adata, n_top_genes=2000, flavor="cell_ranger", batch_key="batch")
```
The book/scvi authors suggest ~1000-6000 (up to 10000) HVGs — trade noise reduction vs rare-cell loss.

## Recommended methods (top scIB performers)
### scVI — unsupervised latent space (raw counts)
```python
import scvi
adata.layers["counts"] = adata.layers.get("counts", adata.X.copy())
scvi.model.SCVI.setup_anndata(adata, layer="counts", batch_key="batch")
m = scvi.model.SCVI(adata); m.train()
adata.obsm["X_scVI"] = m.get_latent_representation()
```
### scANVI — label-aware (best when you have cell labels)
```python
ms = scvi.model.SCANVI.from_scvi_model(m, labels_key="cell_type", unlabeled_category="Unknown")
ms.train(max_epochs=20)
adata.obsm["X_scANVI"] = ms.get_latent_representation()
```
The book: if labels exist and biology is paramount, label-aware methods (scANVI) win across tasks.

## Faster alternatives
```python
sc.external.pp.harmony_integrate(adata, key="batch")          # -> obsm["X_pca_harmony"]
import scanorama  # corrects per-batch; good complex-integration performer
bbknn.bbknn(adata, batch_key="batch", neighbors_within_batch=3)  # modifies the graph directly
```
Harmony/Seurat are strong on *simple* batch correction; scANVI/scVI/Scanorama on *complex* tasks.

## Embedding vs corrected expression
- scVI/scANVI/Harmony give a **corrected embedding** (`obsm[...]`) — use it for neighbors/clustering,
  but keep ORIGINAL expression for DE/markers.
- BBKNN corrects the **graph** only. Seurat `IntegrateData` gives a corrected expression matrix.
- For DE across conditions, do NOT run on corrected values — use pseudobulk on raw counts
  (`bp-differential-expression`).

## Evaluate with scIB (don't eyeball)
`scib-metrics` scores **batch removal** (kBET, graph connectivity, batch silhouette/iLISI) vs
**bio conservation** (NMI, ARI, cell-type silhouette, isolated-label F1). Run several methods,
compare, pick the Pareto winner.

## Downstream
```python
sc.pp.neighbors(adata, use_rep="X_scVI"); sc.tl.umap(adata); sc.tl.leiden(adata)
```

## Pitfalls the book calls out
- **Visualize before correcting** — integration isn't always needed and can overcorrect (erase biology).
- scVI/scANVI need **raw counts** (`layer="counts"`), not normalized.
- Harmonize cell-type labels across batches before scANVI.
- Don't confound batch with the biological variable you care about.

## In ABA
ABA ships ready paths: **`scvi-integration`**, **`scvi-label-transfer-scanvi`**,
**`create-harmony-embeddings-scrna`**, **`create-scvi-embeddings-scrna`**, and
**`conos-integration`** (R/Conos). The integrated embedding feeds **`bp-clustering`** /
**`bp-annotation`**.
