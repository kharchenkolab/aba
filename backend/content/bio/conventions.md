# Bioinformatics file conventions

Conventions that drive the in-app Files view and the on-disk export
layout. The system reads these into the agent's per-turn context so
generated titles align with the layout, and the layout computer in
`content/bio/files/layout.py` mirrors the per-type rules below.

## Names
- snake_case for generated files: `qc_depth_distribution.png`, not `QC Depth.png`.
- ISO dates where dates appear: `2026-05-27`, never `27/05/2026`.
- No spaces, no parentheses, no version suffixes (use the entity graph for versions).
- Keep slugs under ~80 characters; the layout computer truncates.

## Per-entity-type layout
- `figure`     → `figures/{group?}/{title_slug}.png` where group is `qc`, `de`, `embeddings`, or top-level.
- `table`      → `tables/{title_slug}.csv`
- `dataset`    → `datasets/{title_slug}.{ext}`
- `result`     → `results/{title_slug}/`           (a directory of members)
- `analysis`   → `runs/{date}_{title_slug}/`
- `thread`     → `threads/{title_slug}/`
- `claim`      → `claims/{title_slug}.md`
- `narrative`  → `narratives/{title_slug}.md`
- `finding`    → `findings/{title_slug}/`
- `note`       → `notes/{title_slug}.md`

## Group / prefix hints (for `figure` titles)
- `qc_*`          → `figures/qc/`     (QC plots: depth distribution, mt fraction, doublet rate)
- `de_*`          → `figures/de/`     (differential-expression: volcano, MA, top-hits)
- `umap_*`, `tsne_*`, `pca_*` → `figures/embeddings/`
- Otherwise top-level `figures/`

If the title doesn't start with a known prefix, that's fine — generated
titles should describe the metric (e.g., `mt_fraction_per_sample.png`).
The prefix nudge is for clustering related figures, not a hard rule.

## Conventions for run output naming
When a `run_python` block produces multiple plots, save each with a
distinct name describing its content (not `out.png` or `plot.png`):
- `qc_n_genes_per_cell.png`
- `qc_mt_fraction_distribution.png`
- `umap_leiden_resolution_0.5.png`

The registration layer will look at the filename + producing-code
context to infer the figure's title; matching names to titles keeps the
file tree readable.

## Dates and provenance
- Run directories include the ISO date (`runs/2026-05-27_pbmc3k_qc/`).
- Each `finding` directory contains a `provenance.json` summarizing
  the upstream entity-graph subgraph.
- Reproducibility manifests use `reproducibility_manifest.yaml` per
  the template in `inst_aba_base.md §12`.

## Plot defaults
- 150 DPI for working figures, 300 for publication.
- Colorblind-safe palettes (`viridis`, `cividis`) by default.
- Axes always labeled; title contains the metric, not the question.
