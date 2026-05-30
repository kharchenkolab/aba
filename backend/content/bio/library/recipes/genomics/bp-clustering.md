---
name: bp-clustering
description: Best-practice scRNA-seq clustering — Leiden community detection on the KNN graph at multiple resolutions, per the Single-cell Best Practices book.
when_to_use: Use this for the clustering STAGE only when you want rigor beyond a single Leiden call — a resolution sweep, sub-clustering, and cluster-stability/coherence assessment (Leiden over Louvain). For a quick end-to-end first pass (single Leiden resolution) use scrna-qc-clustering; for the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, leidenalg, igraph]
keywords: [clustering resolution sweep, cluster stability, sub-clustering, Leiden vs Louvain, multi-resolution scan, community detection rigor]
produces: [adata_clustered.h5ad, leiden_umap.png]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/cellular_structure/clustering.html"
---

# scRNA-seq clustering (best practice)

Group cells into putative states via community detection on the KNN graph. The book's
recommendation is unambiguous: use **Leiden** (Louvain is no longer maintained and can yield
badly-connected clusters), and **scan multiple resolutions** rather than trusting one.

**Provision:** `ensure_capability(["scanpy","anndata","leidenalg","igraph"])`.

## Build the graph, then Leiden
```python
import scanpy as sc
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)   # or use_rep="X_scVI" if integrated

# scan resolutions; igraph backend is the fast/recommended one
for res in (0.25, 0.5, 1.0):
    sc.tl.leiden(adata, resolution=res, key_added=f"leiden_res{res}",
                 flavor="igraph", n_iterations=2, directed=False)

sc.pl.umap(adata, color=["leiden_res0.25","leiden_res0.5","leiden_res1.0"],
           legend_loc="on data", save="_leiden.png")
```
- **Resolution** is the main knob: higher -> more, finer clusters; lower -> coarser. Default 1.0.
  There is no "correct" value — choose by biological interpretability across the scan.
- `flavor="igraph", n_iterations=2` is the efficient default; `n_iterations=-1` runs to convergence.

## Sub-clustering
To resolve states inside one cluster, subset and re-cluster on a graph rebuilt within the subset:
```python
sub = adata[adata.obs["leiden_res1.0"] == "3"].copy()
sc.pp.neighbors(sub, n_pcs=30); sc.tl.leiden(sub, resolution=0.5)
```
Caution: deep sub-clustering can split on noise — validate splits with markers.

## Assessing clusters
- Color the UMAP by each resolution; check clusters are coherent, not stringy artifacts.
- Cross-check with marker genes (`bp-annotation`) — clusters should map to interpretable identities.
- Remember UMAP inter-cluster distance is not metric; judge separation on the graph, not the picture.

## Pitfalls the book calls out
- **Leiden over Louvain** — Louvain is unmaintained and can produce disconnected communities.
- **One resolution lies** — always scan (e.g. 0.25/0.5/1.0) and save with descriptive keys.
- The KNN graph reflects **expression topology**, not spatial/temporal proximity.
- Over-clustering invents structure; under-clustering merges real types — anchor the choice in biology.
- For million-cell graphs, `rapids-singlecell` accelerates neighbors + Leiden substantially.

## In ABA
Pick the resolution that best matches expected biology, then annotate with **`bp-annotation`**.
If batches are present, build the graph on an integrated embedding from **`bp-data-integration`**
(`use_rep="X_scVI"` / `"X_pca_harmony"`) before clustering.
