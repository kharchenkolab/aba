# Bioinformatics file conventions

These rules drive the in-app Files view, the on-disk materialized layout,
and the file paths used in lab research-repo bundles. The system loads
this into the agent's per-turn context so generated titles and outputs
fit the layout naturally.

## Names
- snake_case for generated files: `qc_depth_distribution.png`, not `QC Depth.png`.
- No spaces, no parentheses, no version suffixes (the entity graph holds versions).
- Keep slugs under ~80 characters; the layout computer truncates.
- **Do NOT embed dates in the filename or folder name** — the file's
  modification time on disk and the entity's `created_at` carry the
  date. Material ordering uses `NN_` numbered prefixes instead.

## Top-level project layout

```
project_root/
  README.md                    # generated
  conventions.md               # snapshot of these rules
  datasets/                    # project inputs (uploaded data)
  threads/                     # the work — one folder per thread
  findings/                    # promoted, cross-thread bundles
  orphans/                     # entities not yet attached to a thread
```

## Ordered containers (numbered prefix)
Anything that's ordered by creation gets a `NN_` prefix; index ordered
by `created_at` ascending:

- Threads under `threads/`: `01_<slug>/`, `02_<slug>/`, …
- Runs within a thread: `runs/01_<slug>/`, `02_<slug>/`, …
- Results within a thread: `results/01_<slug>/`
- Claims as `.md` within a thread: `claims/01_<slug>.md`
- Findings at the project root: `findings/01_<slug>/`

The number is computed dynamically and re-derived when a new sibling
appears; it's never embedded in the entity title.

## Within-container layout

### Run folder
```
runs/01_<run_slug>/
  README.md                # what, when, parameters, what it produced
  producing_code.py        # the code the run executed (when available)
  figures/                 # PNGs the run generated
    <descriptive_name>.png
  tables/                  # CSVs the run generated
    <descriptive_name>.csv
```

### Result folder
```
results/01_<result_slug>/
  README.md                # interpretation + caveats + evidence summary
  <member_file>            # each panel as a symlink to the canonical artifact
```

### Thread folder
```
threads/01_<thread_slug>/
  README.md                # thread question, lifecycle, open Qs, links
  runs/                    # (omitted if none)
  results/                 # (omitted if none)
  claims/                  # (omitted if none)
```

### Finding folder
```
findings/01_<finding_slug>/
  README.md                # the finding bundle prose (per inst_aba_base.md §12)
  evidence/                # supporting figures/tables as symlinks
  provenance.json          # entity-graph subgraph (later)
```

## README at every container
Every container directory (project, thread, run, result, finding) carries
a `README.md`. First pass: mechanically generated from entity metadata +
the list of children. Later: enriched by a filer sub-agent that writes
2–3 paragraph summaries. Both versions are regeneratable.

## Multi-rooting
A file produced by run R and also kept as a member of result Y appears
under both:
- `threads/T/runs/01_R/figures/<f>.png`  (canonical workflow home)
- `threads/T/results/01_Y/<f>.png`        (because it's a member)

In the materialized tree, both paths are symlinks to one canonical
`artifacts/<uuid>.png`. In the virtual view, the same file appears in
both branches of the tree. That's intentional — scientists want to be
able to navigate either by run (workflow) or by result (interpretation).

## Within-file naming guidance
When a `run_python` block produces multiple plots, save each with a
descriptive name (not `out.png`, `plot.png`):
- `qc_n_genes_per_cell.png`
- `qc_mt_fraction_distribution.png`
- `umap_leiden_resolution_0.5.png`

A run's figure folder ends up looking like:

```
runs/01_qc_scatter/figures/
  qc_n_genes_per_cell.png
  qc_mt_fraction_distribution.png
```

## Plot defaults
- 150 DPI for working figures, 300 for publication.
- Colorblind-safe palettes (`viridis`, `cividis`) by default.
- Axes always labeled; title contains the metric, not the question.
