---
name: bp-rna-velocity
description: Best-practice RNA velocity — scVelo dynamical (EM) model on spliced/unspliced counts, with phase-portrait validation and assumption checks, per the Single-cell Best Practices book.
when_to_use: Use this for the RNA-velocity STAGE only — a scRNA-seq dataset WITH spliced + unspliced counts (velocyto/alevin-fry/kb-python loom) capturing an active transient process, where you want directional dynamics from the scVelo dynamical model. For the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, scvelo]
keywords: [RNA velocity, scVelo, dynamical EM model, spliced unspliced, velocyto, phase portrait, CellRank, transient state dynamics]
produces: [adata_velocity.h5ad, velocity_stream.png, phase_portraits.png]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/trajectories/rna_velocity.html"
---

# RNA velocity (best practice)

RNA velocity infers the FUTURE state of each cell from the ratio of unspliced (nascent) to
spliced (mature) mRNA, giving directionality to a trajectory. The book recommends **scVelo's
dynamical (EM) model** but is firm that velocity **only applies to active, transient systems**
with dynamics on the timescale of RNA half-lives — and that results must be validated.

**Provision:** `ensure_capability(["scanpy","anndata","scvelo"])`. Requires spliced + unspliced
layers (from velocyto, alevin-fry, or kb-python with an **augmented** reference — see
`bp-raw-data-processing`). Pass **whole/unprocessed counts** to scVelo.

## Workflow (dynamical model)
```python
import scvelo as scv, scanpy as sc
scv.pp.filter_and_normalize(adata, min_shared_counts=20, n_top_genes=2000)
sc.pp.pca(adata); sc.pp.neighbors(adata)
scv.pp.moments(adata, n_pcs=None, n_neighbors=None)

scv.tl.recover_dynamics(adata, n_jobs=8)         # EM gene-wise kinetics
scv.tl.velocity(adata, mode="dynamical")         # recommended over steady-state/stochastic
scv.tl.velocity_graph(adata, n_jobs=8)
scv.pl.velocity_embedding_stream(adata, basis="umap", save="velocity_stream.png")
```
Modes: `deterministic` (steady-state, simplest), `stochastic`, `dynamical` (EM, recommended —
relaxes the equilibrium assumption).

## Validate the assumptions (do not skip)
```python
scv.pl.scatter(adata, basis=["GENE1","GENE2"], color="leiden")  # phase portraits
```
- Phase portraits should show the expected **almond/loop shape**; if genes show multiple,
  pronounced kinetics, apply velocity with caution.
- Confirm the **process timescale ~ RNA half-life** and that real **transitions** exist.

## When velocity FAILS (book's explicit warnings)
- **Steady-state systems** with no transitions (e.g. resting PBMCs) -> no meaningful velocity.
- **Slow / long-term processes** (Alzheimer's, Parkinson's) -> timescale mismatch.
- Constant-rate kinetic assumption breaks in some systems (e.g. erythroid maturation) even for the
  EM model.

## Pitfalls the book calls out
- The projected **stream depends heavily** on the gene set and plotting params — don't over-read a
  single stream plot.
- Prefer downstream **CellRank** for quantitative, higher-dimensional analysis (fate probabilities)
  rather than relying on the 2D projection alone.
- Newer methods (DeepVelo, etc.) try to relax constant-rate assumptions.

## In ABA
Pair with **`bp-trajectory-inference`** (pseudotime topology) — CellRank fuses velocity + pseudotime
into fate maps. Reference must emit spliced+unspliced (augmented transcriptome,
`bp-raw-data-processing`).
