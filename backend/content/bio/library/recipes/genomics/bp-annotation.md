---
name: bp-annotation
description: Best-practice scRNA-seq cell-type annotation — manual marker-gene assignment, automated classification (CellTypist), and reference label transfer (scArches/scANVI), per the Single-cell Best Practices book.
when_to_use: Use this for the cell-type annotation STAGE — assigning identities to clusters by combining manual marker-gene validation, automated classification (CellTypist), and reference label transfer (scArches/scANVI), treating automated calls as a starting point. For a quick end-to-end first pass that just emits cluster markers use scrna-qc-clustering; for the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, celltypist]
keywords: [cell-type annotation, marker-based annotation, automated cell-type classification, CellTypist, reference mapping, label transfer, scArches, scANVI, score_genes, coarse-to-fine annotation]
produces: [adata_annotated.h5ad, marker_dotplot.png, annotation.csv]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/cellular_structure/annotation.html"
---

# scRNA-seq cell-type annotation (best practice)

Assign biological identities to clusters/cells. The book pairs **automated prediction** (fast,
scalable) with **manual marker validation** (transparent) and warns automated calls are a
*starting point*. Annotate **coarse -> fine** iteratively.

**Provision:** `ensure_capability(["scanpy","anndata","celltypist"])`.

## 1. Manual marker-gene annotation (always do this to validate)
```python
import scanpy as sc
sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
sc.pl.rank_genes_groups_dotplot(adata, n_genes=5, save="_markers.png")

# check a known marker dictionary as a dotplot
markers = {"T cell": ["CD3D","CD3E"], "B cell": ["CD79A","MS4A1"],
           "Mono": ["CD14","LYZ"], "NK": ["NKG7","GNLY"]}
sc.pl.dotplot(adata, markers, groupby="leiden", standard_scale="var")

# per-cell signature scores for a marker set
sc.tl.score_genes(adata, markers["T cell"], score_name="T_score")
```
Map clusters to types from the dotplot, then write labels into `adata.obs["cell_type"]`.
Cluster-level annotation is more robust than per-cell, given marker sparsity/dropout.

## 2. Automated classification — CellTypist
Pre-trained logistic models with multiple resolutions + majority voting.
```python
import celltypist
# CellTypist expects CP10k + log1p input
adata_ct = adata.copy()
sc.pp.normalize_total(adata_ct, target_sum=1e4); sc.pp.log1p(adata_ct)
pred = celltypist.annotate(adata_ct, model="Immune_All_Low.pkl", majority_voting=True)
adata.obs["celltypist"] = pred.predicted_labels["majority_voting"].values
adata.obs["celltypist_conf"] = pred.predicted_labels["conf_score"].values
```
Cross-check `celltypist` labels against the marker dotplot before trusting them.

## 3. Reference label transfer — scArches / scANVI
Map your query onto an annotated reference; transfers labels WITH uncertainty.
```python
# requires RAW counts and the query genes aligned to the reference feature order
# model = sca.models.SCANVI.load_query_data(adata_query, ref_model)
# model.train(max_epochs=...); then weighted_knn_transfer for labels + uncertainty
```
See **`scvi-label-transfer-scanvi`** for ABA's ready scANVI/scArches path. Set an uncertainty
threshold (book uses >0.2 -> "Unknown").

## Pitfalls the book calls out
- **Automated = starting point**, not truth. Always validate against markers; inspect dendrograms.
- **Input format matters**: CellTypist wants CP10k+log1p; scArches/scANVI want **raw counts** with
  genes in the reference's order.
- **Annotate progressively** — coarse lineages first, then subtypes via sub-clustering.
- **Batch effects** distort markers/transfer; integrate first (`bp-data-integration`) and prefer
  references built across datasets.

## In ABA
ABA also ships **`annotate-celltype-scrna`** and **`annotate-celltype-with-panhumanpy`** for
turnkey annotation, and **`unsupervised-celltype-transfer-between-scrna-datasets`** for transfer.
Annotated labels feed condition analyses: **`bp-differential-expression`**,
**`bp-compositional-analysis`**, **`bp-gsea-pathway`**.
