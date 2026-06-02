---
name: scrna-best-practices
description: Index / table-of-contents for the rigorous single-cell RNA-seq best-practice pipeline (Heumos et al., sc-best-practices.org). Routes to the per-stage bp-* recipes — QC, normalization, feature selection, dimensionality reduction, clustering, annotation, DE, compositional, integration, trajectory, velocity. NOT a pipeline to run inline.
when_to_use: Use this when the user wants the FULL, rigorous, principled best-practices single-cell workflow (the "sc-best-practices" / Heumos book), or asks which best-practice step covers a stage. It is a map — invoke `Skill(skill="bp-…")` only on the part(s) the task needs; do NOT run every stage. For a quick end-to-end first pass on one sample, use scrna-qc-clustering instead.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata]
keywords: [best practices, single-cell best practices, sc-best-practices, scRNA best practices, rigorous scRNA pipeline, principled single-cell workflow, end-to-end best-practice workflow, Heumos, scanpy pipeline overview, best-practice index, full pipeline]
produces: []
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org"
---

# Single-cell best-practices pipeline (index)

This is a **table of contents**, not a script. It maps the rigorous scRNA-seq
workflow from the *Single-cell Best Practices* book (Heumos et al.,
sc-best-practices.org) onto ABA's per-stage `bp-*` recipes. Each stage is a
focused recipe with the book's method choices, code idioms, and pitfalls.

**Two ways to use single-cell processing in ABA:**
- **Quick end-to-end first pass on ONE sample** → use **`scrna-qc-clustering`**
  (load → fixed-threshold QC → normalize → HVG → PCA → Leiden → UMAP → markers).
  That recipe is self-contained; you do NOT need this index for it.
- **Rigorous, principled flow** (data-driven QC, deliberate method choices,
  evaluation) → use the `bp-*` parts below. **`Skill(skill="bp-…")`** ONLY on the part(s) the
  task actually needs** — do NOT run every stage. Most requests touch one or two
  stages, not the whole chain.

## The chain (typical order)
Run only the stages your task requires; most analyses start mid-chain on an
existing object. Each line: when the stage matters → which recipe to load via `Skill(skill=...)`.

1. **Raw data → counts** — only if you start from FASTQs or an unfiltered
   matrix (mapping, barcodes, UMIs, empty droplets). → `Skill(skill="bp-raw-data-processing")`.
2. **Quality control** — rigorous, data-driven cell QC: MAD outliers, doublet
   detection, ambient-RNA removal. → `Skill(skill="bp-quality-control")`.
3. **Normalization** — when the default shifted-log isn't enough and you want a
   method matched to the downstream task (scran size factors, Pearson residuals).
   → `Skill(skill="bp-normalization")`.
4. **Feature selection** — principled HVG choice; deviance on raw counts to dodge
   normalization sensitivity. → `Skill(skill="bp-feature-selection")`.
5. **Dimensionality reduction** — PCA as the compute representation vs UMAP/t-SNE
   as view-only embeddings. → `Skill(skill="bp-dimensionality-reduction")`.
6. **Clustering** — Leiden with a resolution sweep, sub-clustering, stability
   checks. → `Skill(skill="bp-clustering")`.
7. **Annotation** — assign cell types via markers + automated calls + reference
   transfer. → `Skill(skill="bp-annotation")`.

### Downstream / conditional branches (use the one your question needs)
- **Differential expression across conditions** — genes changing between
  conditions within a cell type, via pseudobulk (never per-cell Wilcoxon for
  condition DE). → `Skill(skill="bp-differential-expression")`.
- **Compositional analysis** — whether cell-type *proportions* shift across
  conditions (scCODA / Milo). → `Skill(skill="bp-compositional-analysis")`.
- **Integration / batch correction** — multiple batches/donors into a shared
  space (scVI/scANVI, Harmony, scIB evaluation). → `Skill(skill="bp-data-integration")`.
- **Trajectory inference** — pseudotime + branch topology for a continuous
  process (DPT/PAGA/Palantir). → `Skill(skill="bp-trajectory-inference")`.
- **RNA velocity** — directional dynamics from spliced/unspliced counts (scVelo).
  → `Skill(skill="bp-rna-velocity")`.
- **Gene-set enrichment / pathway & TF activity** — pathways from DE results or
  per-cell activity scores (decoupler). → `Skill(skill="bp-gsea-pathway")`.

### Other modalities (not the scRNA chain)
- **CITE-seq / surface protein (ADT)** — paired RNA + antibody tags, muon/MuData,
  ADT-specific QC + DSB/CLR. → `Skill(skill="bp-cite-seq")`.
- **scATAC-seq (chromatin accessibility)** — snapATAC2, TF-IDF/LSI, gene activity,
  motifs. → `Skill(skill="bp-atac")`.

## How to use this in a plan
1. Identify which stage(s) the user's request touches (often just one).
2. Invoke `Skill(skill="bp-...")` only on those `bp-*` recipes; lift their code/idioms.
3. `ensure_capability([...])` for whatever those parts declare.
4. `present_plan` before running on an unfamiliar dataset — thresholds, method
   choices, and resolutions are dataset-dependent.

Do NOT treat this index as a single runnable pipeline: an agent that runs every
stage will over-process the data and pick wrong methods. Keep it granular.
