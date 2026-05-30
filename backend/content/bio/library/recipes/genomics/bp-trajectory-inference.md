---
name: bp-trajectory-inference
description: Best-practice scRNA-seq pseudotemporal ordering — diffusion pseudotime (DPT) and PAGA topology in scanpy, plus Palantir, with root-cell choice and validation, per the Single-cell Best Practices book.
when_to_use: Use this for the trajectory / pseudotime STAGE only — a continuous/developmental scRNA-seq process (differentiation, maturation) where you want to order cells along pseudotime and recover branch topology (DPT/PAGA/Palantir). For the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, palantir]
keywords: [trajectory inference, pseudotime, pseudotemporal ordering, diffusion pseudotime, DPT, PAGA, Palantir, Slingshot, root cell, branching lineage]
produces: [adata_pseudotime.h5ad, paga_graph.png, pseudotime_umap.png]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/trajectories/pseudotemporal.html"
---

# scRNA-seq pseudotemporal ordering (best practice)

Order cells along a continuous biological process and recover its topology. The book stresses:
**know the process first** (linear / cyclic / branching), trajectory methods ASSUME continuity,
and different algorithms disagree — so **compare methods and validate biologically**.

**Provision:** `ensure_capability(["scanpy","anndata","palantir"])`.

## Preprocess (standard, then a diffusion map)
```python
import scanpy as sc
sc.pp.normalize_total(adata); sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata); sc.pp.pca(adata)
sc.pp.neighbors(adata, n_pcs=10)
sc.tl.diffmap(adata)            # diffusion components for DPT + root selection
```

## PAGA — coarse topology (which clusters connect)
```python
sc.tl.paga(adata, groups="leiden")
sc.pl.paga(adata, save="_graph.png")
sc.tl.umap(adata, init_pos="paga")   # PAGA-initialized embedding respects topology
```
PAGA abstracts the graph to cluster-level connectivity — a robust first read on branching.

## DPT — diffusion pseudotime (needs a root cell)
```python
# pick a root from an extreme of a diffusion component (or a known progenitor cluster)
import numpy as np
adata.uns["iroot"] = int(np.argmin(adata.obsm["X_diffmap"][:, 3]))
sc.tl.dpt(adata)
sc.pl.umap(adata, color="dpt_pseudotime", save="_pseudotime.png")
```

## Palantir — probabilistic, multi-branch terminal states
Models cells as a Markov chain; terminal states are absorbing states. The book found Palantir's
pseudotime increased monotonically with maturity where DPT inflated in places.
```python
import palantir
# pr_res = palantir.core.run_palantir(adata, early_cell=root_cell_name, ...)
```
Slingshot (R, principal curves) and Monocle are further options the book covers.

## Pitfalls the book calls out
- **A root cell is required** — choose from biological knowledge or a diffusion-component extreme;
  a wrong root reverses/garbles pseudotime.
- Methods assume **continuity** — don't force a trajectory onto discrete, unrelated cell types.
- **Method-dependent results** — compare DPT vs Palantir vs PAGA; use `dynguidelines` to match a
  method to your topology.
- **Validate**: color embeddings, plot pseudotime distributions per annotated type (violins),
  check against known developmental order.

## In ABA
Builds on the embedding from **`bp-dimensionality-reduction`** / **`bp-data-integration`** and
labels from **`bp-annotation`**. For directional dynamics from spliced/unspliced counts, see
**`bp-rna-velocity`** (and CellRank to combine velocity + pseudotime).
