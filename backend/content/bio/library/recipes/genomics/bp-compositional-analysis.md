---
name: bp-compositional-analysis
description: Best-practice scRNA-seq compositional analysis — testing cell-type proportion shifts across conditions with scCODA/tascCODA (pertpy) or Milo, accounting for compositionality, per the Single-cell Best Practices book.
when_to_use: Use this for the compositional / differential-abundance STAGE only — multi-sample scRNA-seq with a condition where you want to know whether CELL-TYPE PROPORTIONS change (not gene expression), via a compositional-aware model (scCODA) or KNN-neighborhood DA (Milo). For the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, pertpy, milopy]
keywords: [compositional analysis, cell-type proportion shift, differential abundance, scCODA, tascCODA, pertpy, Milo, milopy, Dirichlet multinomial, reference cell type, compositionality]
produces: [composition_results.csv, proportions_barplot.png, credible_effects.csv]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/conditions/compositional.html"
---

# scRNA-seq compositional analysis (best practice)

Question: do **cell-type proportions** shift between conditions? The catch is
**compositionality** — proportions sum to 1, so one type rising forces others to fall
(induced negative correlation). Naive per-type tests (Wilcoxon on fractions) therefore produce
false positives. The book uses **scCODA** (labeled types) or **Milo** (label-free, KNN neighborhoods).

**Provision:** `ensure_capability(["scanpy","anndata","pertpy","milopy"])`. Needs **biological
replicates** per condition.

## With labeled cell types -> scCODA (pertpy)
Bayesian Dirichlet-multinomial model that respects compositionality and tolerates few replicates.
It tests effects **relative to a reference cell type** (assumed unchanged).
```python
import pertpy as pt
sccoda = pt.tl.Sccoda()
cdata = sccoda.load(adata, type="cell_level", generate_sample_level=True,
                    cell_type_identifier="cell_type", sample_identifier="sample_id",
                    covariate_obs=["condition"])
sccoda.prepare(cdata, modality_key="coda", formula="condition",
               reference_cell_type="automatic")     # or a type you believe is stable
sccoda.run_nuts(cdata, modality_key="coda")
sccoda.set_fdr(cdata, est_fdr=0.05)                  # raise toward 0.2 for sensitivity
sccoda.credible_effects(cdata, modality_key="coda").to_csv("credible_effects.csv")
```
Check MCMC acceptance is ~0.4-0.9 (valid sampling). The **reference cell type** sets the frame of
reference — pick one unchanged by the condition, or `"automatic"`.

## With a cell hierarchy / atlas resolution -> tascCODA (pertpy)
Tree-aggregated scCODA: tests effects on tree NODES (groups of related types), useful for
high-resolution atlases.
```python
tasccoda = pt.tl.Tasccoda()
# load with levels_orig=[...fine -> coarse...]; prepare(..., pen_args={"phi":0,"lambda_1":3.5})
```

## Without labels -> Milo (KNN-neighborhood differential abundance)
Tests abundance changes on overlapping KNN neighborhoods (no clustering needed) via a NB-GLM —
catches shifts in transitional/continuous populations.
```python
import milopy
milopy.core.make_nhoods(adata)
milopy.core.count_nhoods(adata, sample_col="sample_id")
milopy.core.DA_nhoods(adata, design="~condition")
```
(DA-seq, MELD are related label-free options the book mentions.)

## Pitfalls the book calls out
- **Don't use univariate tests** (Wilcoxon) on proportions — compositionality -> false positives.
- scCODA needs a sensible **reference cell type**; results are read relative to it.
- Need **enough samples AND cells**; scCODA is built for low replication but not n=1.
- FDR: start strict (0.05), relax to ~0.2 only to surface prominent effects.

## In ABA
`pertpy` provides both scCODA and tascCODA; `milopy` for neighborhood DA. Pair with
**`bp-differential-expression`** (what changes in expression) — composition answers *how many*,
DE answers *how different*.
