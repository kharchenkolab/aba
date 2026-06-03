---
name: scrna-qc-clustering-v2
description: Standard scanpy processing for ONE scRNA-seq sample — QC, filtering, normalization, highly variable genes, PCA, Leiden clustering, UMAP and cluster markers. Tissue/species-agnostic recipe with data-driven QC thresholds visualized on the cells they remove, PCA elbow showing per-PC and cumulative variance, and a top-5 marker dotplot + canonical-marker UMAP overlay for cluster characterization.
when_to_use: You have a single scRNA-seq sample (10x mtx / h5ad / raw count matrix) and want to process it with scanpy — basic/standard processing and a first-pass clustering (QC → normalize → cluster → UMAP → markers) before any biology. For principled MAD-based QC instead, see bp-quality-control; for FASTQ→counts see bp-raw-data-processing. For an R/Seurat session use the analogous seurat-scrna-v2 recipe (same biology, same steps, shared visual language).
avoid_when: "Multiple samples/donors you intend to INTEGRATE jointly (use harmony/scvi/seurat-integration — do not concat-then-cluster); bulk RNA-seq; only a gene list with no count matrix; cross-condition DE across donors (needs pseudobulk + a DE recipe, not this single-sample clustering)."
requires_tools: [run_python]
capabilities_needed: [scanpy, leidenalg]
keywords: [scanpy, process, processing, preprocess, preprocessing, basic processing, standard scanpy pipeline, scanpy workflow, single cell, scRNA-seq, normalize, normalization, log1p, clustering, leiden, UMAP, PCA, highly variable genes, marker genes, cell QC, quality control, filtering, first-pass, end-to-end, pct_counts_mt, rank_genes_groups]
produces: [qc_violins_pre.png, qc_scatters_pre.png, hvg_plot.png, pca_elbow.png, umap_clusters.png, markers_dotplot.png, markers_featureplot.png, cluster_markers.csv, processed.h5ad]
domain: genomics
resource_profile: small-medium  (~30s for 10–50k cells)
---

# scRNA-seq single-sample QC + clustering with scanpy

Generic single-sample recipe — works for **any tissue, any organism** for which
you have a counts matrix. We use 10x PBMC as the running example only because it
is familiar; **none of the thresholds, marker lists, or cell-type labels below
are universal** — every concrete number is dataset-dependent and the recipe
says, at each step, what to look at to pick a sensible value for *your* data.

Prefer this (scanpy/Python) when the session is already Python, the user asks
for scanpy, or downstream tools are Python-native (scVI, CellRank). For an
R-native session use `seurat-scrna-v2` — same biology, same steps, same visual
language.

**Provision once:** `ensure_capability("scanpy")` and `ensure_capability("leidenalg")`.
Then in `run_python`:

```python
import scanpy as sc
import anndata as ad
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
```

## Multiple samples: keep them SEPARATE — do NOT naively concatenate

Several samples/donors/runs are **multiple datasets, not one matrix**.
`sc.concat`-ing raw counts and clustering the result confounds batch with
biology (the clusters just separate by sample). "Register them **together**" =
ONE dataset entity spanning the per-sample files, NOT a merged matrix. Combine
samples only as the explicit first step of a **batch-aware integration**
(`harmony-integration-scanpy`, `scvi-integration`, `conos-integration`) — never
a concatenate-then-cluster shortcut. One sample = this recipe; two+ jointly =
an integration recipe.

**Honor the requested scope — don't upsell integration.** This guardrail stops
*naive concatenation*; it is NOT a reason to push integration. If the user asks
to process one sample (e.g. "the second sample"), run this recipe on exactly
that sample and stop. Other samples existing is not a cue to propose batch
correction — integration is a separate, explicit request.

---

## The four decisions that define the result

Surface these in your plan (`present_plan`) before running anything. The
defaults given later are *starting points*, not answers.

1. **Species / MT prefix** — `MT-` for human (uppercase), `mt-` for mouse,
   `Mt-` or organism-specific for others. Get this wrong and your `pct_counts_mt`
   is 0 and you'll keep dying cells.
2. **QC thresholds** — `n_genes_by_counts` (low and high) and `pct_counts_mt`
   cutoffs. These define which cells are real. Pick from the distributions, not
   from a prior dataset.
3. **Number of PCs (`n_pcs`)** — feeds the neighbor graph and UMAP. Read off
   the elbow; 20–30 is a safe default when the elbow is unclear.
4. **Clustering resolution** — `sc.tl.leiden(resolution=…)` controls cluster
   count. 0.5 is moderate; 0.2–0.3 coarser, 0.8–1.2 finer.

---

## Figure style

The figures in this pipeline share a deliberate visual language. The full code
blocks live with each step below, but the choices that distinguish them from
scanpy's stock defaults are restated here as a checklist — re-read the relevant
bullet immediately before you write each figure's code, so the styling survives
translation from "I read the recipe" to "I write matplotlib". These are
**overrides** for the global figure defaults (`figures.md`); where this section
is silent, the global defaults apply.

**Universal**
- **First line of the run:** `sc.settings.figdir = '.'`. Otherwise `sc.pl.*(save=…)`
  writes to a `figures/` subdir the harness does NOT harvest and the plots never
  appear. Hand-built figures: `plt.savefig('name.png', dpi=120, bbox_inches='tight')`
  into the cwd.
- Set scanpy's global theme once, at the top of the run, so every `sc.pl.*` call
  inherits it:
  ```python
  sc.set_figure_params(dpi=120, dpi_save=120, frameon=True,
                       fontsize=11, figsize=(7, 5), facecolor='white')
  plt.rcParams.update({
      'axes.titlesize': 12, 'axes.titleweight': 'bold',
      'axes.spines.top': False, 'axes.spines.right': False,
      'axes.grid': True, 'grid.color': '#e5e5e5', 'grid.linewidth': 0.3,
      'figure.facecolor': 'white', 'savefig.facecolor': 'white',
      'savefig.bbox': 'tight',
  })
  ```
- Titles 12pt bold; subtitles via `ax.set_title(..., loc='left')` in grey40
  where used. No minor grid.

**Color**
- **Diverging signal** — anything centered on zero (dotplot's `logfoldchanges`,
  any signed-expression heatmap):
  ```python
  RDBU = LinearSegmentedColormap.from_list(
      'rdbu_centered', ['#2166ac', 'grey95', '#b2182b'])
  ```
  Pass as `cmap=RDBU` (and `vcenter=0` where the plotting call supports it).
  Don't substitute viridis/plasma here — they are sequential by design and
  squash the meaning of "zero" on signed metrics.
- **Sequential signal** — UMAP-coloured-by-expression, dotplot's `mean expression`,
  single-sided positives:
  ```python
  REDS = LinearSegmentedColormap.from_list(
      'reds_white', ['grey95', '#b2182b'])
  ```
  Neutral end near-white, signal in saturated red. Pass as `cmap=REDS`.
- **Categorical cluster palette** — for UMAP-by-cluster, use the `tab20` palette
  via `sc.pl.umap(..., palette='tab20')`; reproducible across runs and stays
  readable up to ~20 clusters. Beyond that, switch to a curated qualitative
  palette per cluster.

**Dense scatter / jitter** — QC scatters, jitter on QC violins
- Hand-rolled figures (these can't be done with `sc.pl.violin` because we need
  to color jitter points by `qc_kept`, an external boolean): `alpha=0.10` (yes,
  that low — let density carry the visual weight), `s=4` for scatters,
  `s=1.5` for violin jitter.
- Two-tone kept/filtered: `c={'kept':'black', 'filtered':'red'}`. Add the
  legend by hand with `Line2D` proxies at `alpha=1, markersize=6` so the
  legend dots are visible.
- Threshold lines: `ax.axhline(value, color='red', linestyle='--', linewidth=0.5)`
  (or `axvline` on rotated axes).

**QC violins**
- Use `seaborn.violinplot` (or a hand-rolled matplotlib violin) rather than
  `sc.pl.violin` — scanpy's violin can't overlay an external kept/filtered
  boolean on the jitter, which is the whole point of this figure.
- Per-metric fill from a qualitative palette: Set2 (`sns.color_palette('Set2')`),
  no legend (the violin shape carries the meaning).
- Facet metrics top-to-bottom as one column of horizontal violins so the four
  metrics line up vertically; this matches the Seurat figure exactly and
  makes side-by-side comparison across the two recipes trivial.
- Add jittered points colored by `qc_kept` + threshold lines so the figure
  shows BOTH the distribution and which cells the cutoffs will remove.
- Plot multiple violin plots next to each other horizontally.
- Save: `figsize=(7, 4.7)` 

**HVG plot**
- Use `sc.pl.highly_variable_genes(adata, show=False, save='_hvg.png')` — it
  returns a 2-panel figure (mean-vs-dispersion, with and without normalization).
  Don't recreate from scratch.
- For the label overlay of top-10 HVGs: after the call, grab the current axes
  via `plt.gcf().axes`, scatter-annotate the top-10 gene names on the
  right-hand panel using `ax.annotate(gene, (x, y))`. This is a 6-line addition;
  not a rewrite.
- Title via `plt.gcf().suptitle(f"HVG selection (flavor={flavor}, top {nh} of {nv} genes); top 10 labeled")`.
- Save `figsize=(8, 5.5)`.

**PCA elbow** (NOT a standard scree plot — read this carefully)
- A SINGLE PLOT (same two axes, ONE figure) showing TWO curves on the same panel:
  the per-PC variance ratio AND its running cumulative sum, drawn on the same
  x-axis (principal component index) and the same y-axis (% of total variance).
  Do NOT draw a one-curve scree plot. Do NOT split into two side-by-side
  panels, or use two y axes . Do NOT use `sc.pl.pca_variance_ratio` (it shows only the per-PC
  curve and crops to its own scale). This two-curves-one-plot
  layout is intentional — the comparison only works on a
  shared y-axis. Skeleton:
  ```python
  fig, ax = plt.subplots(figsize=(7, 4.8))
  x = np.arange(1, len(var_ratio) + 1)
  ax.plot(x, var_ratio*100, marker='o', markersize=3, linewidth=1.2,
          color='#1f77b4',                            label='per-PC variance (%)')
  ax.plot(x, np.cumsum(var_ratio)*100, linestyle='--', linewidth=1.2,
          color='grey40',                             label='cumulative variance (%)')
  ax.legend(loc='upper right', frameon=True, framealpha=0.5)
  ```
- Distinct colors for the two curves: per-PC = solid blue (`#1f77b4`),
  cumulative = grey dashed (`grey40`). The dashed style is part of the
  distinction (linetype carries information when colors get desaturated in a
  printed copy).
- Y-axis as percent of TOTAL HVG-matrix variance (not of the 50 PCs' variance) —
  multiply by 100 and use `'%.0f%%'` formatter. This makes the cumulative curve
  plateau at the true fraction the PCs capture (~30% is typical), instead of
  misleadingly hitting 100%.
- Annotate the chosen `n_pcs` with a red dashed `ax.axvline(N_PCS_CHOSEN, ...)`
  + a small `ax.text(..., f"n_pcs = {N_PCS_CHOSEN} (chosen)", color='red')` to
  its left.
- Subtitle = heuristic markers: 1% / 0.5% / 2nd-diff knee — context only, not a
  decision rule (`ax.set_title(..., loc='left', fontsize=9, color='grey40')`).
- Legend inside the plot area, top-right; `frameon=True, framealpha=0.5`.
- Save `figsize=(7, 4.8)`.

**UMAP (sc.pl.umap)**
- `sc.pl.umap(adata, color='leiden', legend_loc='on data', legend_fontsize=10,
  legend_fontoutline=2, frameon=True, size=20, alpha=0.6, palette='tab20',
  title=f"Leiden res={res} · n_pcs={n_pcs} · n={adata.n_obs} cells · {n_clusters} clusters",
  show=False, save='_clusters.png')`.
- `alpha=0.6` is non-default and matters: scanpy's default opaque dots
  obscure structure where points overlap. Don't drop it.
- `legend_loc='on data'` prints each cluster label ON the embedding — the
  robust way to show the cluster legend (a right-margin legend with many
  clusters gets clipped when the figure is saved).
- Save dimensions come from `sc.set_figure_params(figsize=(7, 6))` for this
  call; override `figsize` per-call if you need a taller/narrower frame.

**Dotplot (cluster markers)**
- `sc.pl.rank_genes_groups_dotplot(adata, n_genes=5, values_to_plot='logfoldchanges',
  cmap=RDBU, vcenter=0, vmin=-3, vmax=3, swap_axes=False, dendrogram=False,
  show=False, save='_markers.png')`.
- `values_to_plot='logfoldchanges'` + diverging palette is the analog of
  Seurat's `avg_log2FC` dotplot — the default
  `mean_expression_in_group` is a sequential metric and washes out the
  cluster-vs-rest signal. Set `vmin/vmax` explicitly so a single huge logFC
  doesn't compress the color scale.
- `dendrogram=False` keeps clusters in their numeric order (the user's mental
  model from the UMAP); turn it on only if you've already accepted the
  reorder downstream.
- These tend to get large with the number of clusters, so give it more space depending on the number of clusters

**Canonical-marker UMAP overlay (the FeaturePlot analog)**
- 4–8 hand-picked canonical markers (one per major lineage). Don't paint a wall
  of top-N marker UMAPs — that's the scanpy-tutorial pattern this recipe
  explicitly avoids.
- `sc.pl.umap(adata, color=canonical, ncols=3, cmap=REDS, size=15, alpha=0.6,
  frameon=True, show=False, save='_canonical.png')`.
- `sc.pl.umap` accepts a list of genes and lays them out as a grid;
  `vmin/vmax='p1'/'p99'` (1st/99th percentile clipping) prevents one
  ultra-high-expressing cell from washing out the color scale.

**Pitfalls to avoid**
- Don't substitute viridis/plasma where the recipe specifies the diverging
  blue-grey-red — viridis is sequential and erases the meaning of "zero" on
  signed metrics like `logfoldchanges`.
- Don't `sc.pp.scale(adata)` in place — PCA runs fine on log-normalized HVGs,
  and scaling X in place silently corrupts `rank_genes_groups` and gene
  overlays (they need real expression, not z-scores).
- Don't drop `alpha=0.6` on `sc.pl.umap` — the default opaque dots hide
  structure where points overlap.
- Don't put the elbow's per-PC and cumulative curves on separate panels — the
  comparison only works on a shared y-axis.
- Don't use the stock `sc.pl.violin` for the QC violin figure — it can't
  overlay the kept/filtered split, which is the whole point of that figure.

---

## Procedure

### 1. Load the counts matrix

Files live under `DATA_DIR` — get the path from `list_data_files()`; do NOT
guess. Two common 10x layouts:

```python
import os, scanpy as sc, pandas as pd
sc.settings.figdir = '.'    # MUST be first; see Figure style header

D = os.environ['DATA_DIR']

# (a) Standard CellRanger DIR (barcodes/features/matrix.mtx[.gz] inside a folder):
# adata = sc.read_10x_mtx(f"{D}/<sample_dir>", var_names='gene_symbols', cache=True)

# (b) GEO LOOSE, GSM-PREFIXED triplet (…matrix.mtx.gz / …barcodes.tsv.gz /
#     …features.tsv.gz sitting flat in one dir): read_10x_mtx will NOT find
#     these (non-standard names), so read the three parts EXPLICITLY:
pre = "<GSM..._sample_prefix>"
adata = sc.read_mtx(f"{D}/{pre}.matrix.mtx.gz").T               # mtx is genes×cells → transpose
adata.obs_names = pd.read_csv(f"{D}/{pre}.barcodes.tsv.gz",
                              header=None)[0].values
adata.var_names = pd.read_csv(f"{D}/{pre}.features.tsv.gz",
                              header=None, sep='\t')[1].values  # col 2 = symbols
adata.var_names_make_unique()                                    # do NOT skip
```

**Sanity-check the load immediately — three lines that catch 90% of "why is my
QC empty?" failures**:

```python
print(adata)                                              # shape, X dtype, obs/var keys
print("first 5 gene symbols:", list(adata.var_names[:5])) # symbols or Ensembl IDs?
mt_prefix = 'MT-'                                         # 'mt-' for mouse
n_mt = adata.var_names.str.upper().str.startswith(mt_prefix).sum()
print(f"genes matching '{mt_prefix}*': {n_mt}")           # expect ~13 (human) or ~37 (mouse)
```

If gene names look like `ENSG00000…`, you read column 1 instead of column 2 of
`features.tsv.gz` — go back and fix `var_names` before doing anything else.
If `n_mt == 0`, your MT prefix is wrong for this organism — fix it before QC.

**Optional sparsity prefilter** (Seurat's `min.cells = 3, min.features = 200`
equivalent — drops empty droplets and barely-expressed genes BEFORE QC plots,
so the figures show real cells):

```python
n_obs_raw, n_var_raw = adata.shape
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
print(f"after sparsity prefilter: {adata.n_obs} cells (-{n_obs_raw - adata.n_obs}), "
      f"{adata.n_vars} genes (-{n_var_raw - adata.n_vars})")
```

Report the delta — on a clean 10x matrix you typically lose <5% of cells here;
losing >20% means the matrix is unfiltered raw droplets and you should look at
the count distribution before going further.

### 2. QC metrics — compute, then pick thresholds from the *actual* distribution

Compute the metrics, then **print quantile tables BEFORE deciding cutoffs**.
The defaults below (`n_genes ∈ [200, 5000]`, `pct_mt < 15`) are PBMC starting
points; you confirm or revise them from the quantile readout.

```python
adata.var['mt']   = adata.var_names.str.upper().str.startswith('MT-')
adata.var['ribo'] = adata.var_names.str.upper().str.startswith(('RPS', 'RPL'))
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt', 'ribo'],
                           percent_top=None, log1p=False, inplace=True)

# Quantile tables — the readout that drives the THRESH dict below
qs_hi = [0.50, 0.75, 0.90, 0.95, 0.975, 0.99]
qs_lo = [0.005, 0.01, 0.025, 0.05, 0.10]
m = adata.obs[['n_genes_by_counts', 'total_counts',
               'pct_counts_mt', 'pct_counts_ribo']]
print("--- HIGH quantiles ---")
print(m.quantile(qs_hi).round(1))
print("\n--- LOW quantiles (n_genes / total_counts) ---")
print(m[['n_genes_by_counts', 'total_counts']].quantile(qs_lo).round(1))

# Pick from the table, then commit:
THRESH = {
    'n_genes_low':  200,    # ≈ 1st-percentile floor; ↑ for high-quality runs
    'n_genes_high': 5000,   # > 99th percentile — cuts the doublet tail
    'pct_mt_high':  15,     # PBMC 10–15; fresh tissue 20; nuclei 5; tumor 25
}
adata.obs['qc_kept'] = (
    (adata.obs['n_genes_by_counts'] > THRESH['n_genes_low']) &
    (adata.obs['n_genes_by_counts'] < THRESH['n_genes_high']) &
    (adata.obs['pct_counts_mt']     < THRESH['pct_mt_high'])
).map({True: 'kept', False: 'filtered'})

print("\nPre-filter tally:")
print(adata.obs['qc_kept'].value_counts())
```

**Read the quantile table like this**:
- `n_genes_by_counts` high cutoff — set it past the 99th percentile (or ~2×
  the median, whichever is higher). Below the 99th and you're cutting biology;
  much past it and the cutoff isn't doing anything.
- `n_genes_by_counts` low cutoff — `200` is a sparsity floor for the matrix;
  if the 1st percentile is well above 200, your matrix is already
  pre-filtered and you don't need a low cutoff at all.
- `pct_counts_mt` — look at the 95th and 99th percentiles. PBMC 95th is
  typically ~10%; the heavy tail past that is dying cells. Set the cutoff in
  the 12–20 range so you remove the tail without cutting the bulk.
- `pct_counts_ribo` — high in T-cell-rich PBMC (median 15–25%); **do NOT
  filter on this** unless you see a clear bimodal split.

If your quantiles look very different from PBMC expectations (e.g. `pct_mt`
median > 10), STOP and look — it usually means wrong MT prefix, wrong
organism, or a different tissue, and the defaults will not apply.

### 3. QC figures → apply filter

The QC figures show the cells the cutoffs will remove (red) on top of the
ones they will keep (black) — the visualization exposes whether your
thresholds intercept the populations you actually want to remove (a clean
red tail, not red dots scattered through the center).

```python
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# --- Pre-filter violins (4 metrics × kept/filtered overlay + threshold lines) ---
metrics = ['n_genes_by_counts', 'total_counts', 'pct_counts_mt', 'pct_counts_ribo']
thresh_lines = {
    'n_genes_by_counts': [THRESH['n_genes_low'], THRESH['n_genes_high']],
    'pct_counts_mt':     [THRESH['pct_mt_high']],
}
set2 = sns.color_palette('Set2', n_colors=len(metrics))

fig, axes = plt.subplots(len(metrics), 1, figsize=(7, 4.7), sharex=False)
for ax, met, col in zip(axes, reversed(metrics), reversed(set2)):
    # horizontal violin
    parts = ax.violinplot(adata.obs[met].values, vert=False, widths=0.85,
                          showmeans=False, showmedians=False, showextrema=False)
    for body in parts['bodies']:
        body.set_facecolor(col); body.set_edgecolor('black')
        body.set_alpha(0.85); body.set_linewidth(0.4)
    # jitter, colored by qc_kept
    y = 1 + (np.random.RandomState(0).rand(adata.n_obs) - 0.5) * 0.64
    cmap_kept = {'kept': 'black', 'filtered': 'red'}
    cols = adata.obs['qc_kept'].map(cmap_kept).values
    ax.scatter(adata.obs[met].values, y, s=1.5, c=cols, alpha=0.10,
               linewidths=0, rasterized=True)
    # threshold lines
    for v in thresh_lines.get(met, []):
        ax.axvline(v, color='red', linestyle='--', linewidth=0.5)
    ax.set_yticks([]); ax.set_ylabel(met, rotation=0, ha='right',
                                     va='center', fontweight='bold')
    ax.grid(axis='x', color='grey85', linewidth=0.3); ax.set_axisbelow(True)
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
axes[0].set_title(f"QC metrics (n = {adata.n_obs} cells)",
                  fontsize=12, fontweight='bold', loc='left')
handles = [Line2D([0],[0], marker='o', color='w', markerfacecolor=c,
                  markersize=6, label=l) for l, c in cmap_kept.items()]
fig.legend(handles=handles, loc='lower center', ncols=2, frameon=False,
           bbox_to_anchor=(0.5, -0.02))
fig.tight_layout(); fig.savefig('qc_violins_pre.png', dpi=120,
                                bbox_inches='tight'); plt.close(fig)

# --- Pre-filter scatters: counts vs mt, counts vs genes ---
fig, ax = plt.subplots(1, 2, figsize=(12, 5.3))
for a, (xcol, ycol, hlines, title) in zip(ax, [
    ('total_counts', 'pct_counts_mt',
     [THRESH['pct_mt_high']],
     f"total_counts vs pct_mt (cutoff {THRESH['pct_mt_high']}%)"),
    ('total_counts', 'n_genes_by_counts',
     [THRESH['n_genes_low'], THRESH['n_genes_high']],
     f"total_counts vs n_genes (cutoffs {THRESH['n_genes_low']}, {THRESH['n_genes_high']})"),
]):
    for label, color in [('kept', 'black'), ('filtered', 'red')]:
        sub = adata.obs[adata.obs['qc_kept'] == label]
        a.scatter(sub[xcol], sub[ycol], s=4, c=color, alpha=0.10,
                  linewidths=0, rasterized=True, label=label)
    for v in hlines:
        a.axhline(v, color='red', linestyle='--', linewidth=0.5)
    a.set(xlabel=xcol, ylabel=ycol)
    a.set_title(title, fontsize=12, fontweight='bold', loc='left')
    a.grid(axis='y', color='grey85', linewidth=0.3); a.set_axisbelow(True)
    leg = a.legend(loc='upper right', frameon=False)
    for h in leg.legend_handles: h.set_alpha(1); h.set_sizes([24])
fig.suptitle("QC scatters", fontsize=13, fontweight='bold',
             x=0.02, ha='left')
fig.tight_layout(); fig.savefig('qc_scatters_pre.png', dpi=120,
                                bbox_inches='tight'); plt.close(fig)

# --- Apply filter ---
n_before = adata.n_obs
adata = adata[adata.obs['qc_kept'] == 'kept'].copy()
sc.pp.filter_genes(adata, min_cells=3)  # re-prune genes now zero across kept cells
print(f"filter: {n_before} -> {adata.n_obs} cells "
      f"(-{n_before - adata.n_obs}, {100*(n_before - adata.n_obs)/n_before:.1f}%)")

# Sanity check — if you lost > 20% of cells, your thresholds were too strict
pct_lost = 100 * (n_before - adata.n_obs) / n_before
if pct_lost > 20:
    print(f"WARNING: lost {pct_lost:.1f}% of cells. "
          f"Re-inspect the quantile table — thresholds may be too strict, "
          f"or the dataset has a real quality problem.")
```

The QC figures answer "which cells am I about to remove, and why?" — they
should show a clear, contiguous red mass at the high-`pct_mt` tail and at
the high-`n_genes` tail (doublets), not a scatter of red dots through the
center of the distribution. If the red is interleaved with the kept cells,
your cutoffs are wrong.

The "lost >20%" warning is a backstop — biology doesn't usually justify
discarding that many cells, so re-inspect the quantile table before moving on.

### 4. Normalize, log1p, find HVGs

```python
adata.layers['counts'] = adata.X.copy()              # keep raw counts; rank_genes wants them
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)

sc.pp.highly_variable_genes(adata, flavor='seurat', n_top_genes=2000)
nh = int(adata.var['highly_variable'].sum()); nv = adata.n_vars
print(f"HVGs: {nh} / {nv}")

# Sanity check on the top-20 — should NOT be dominated by MT/RPS/RPL/MALAT1
top20 = (adata.var.loc[adata.var['highly_variable']]
                  .sort_values('dispersions_norm', ascending=False)
                  .head(20).index.tolist())
print("top 20 HVGs:", top20)
flag = {
    'mt':    sum(g.upper().startswith('MT-') for g in top20),
    'rib':   sum(g.upper().startswith(('RPS','RPL')) for g in top20),
    'malat': sum(g.upper() == 'MALAT1' for g in top20),
    'hb':    sum(g.upper().startswith(('HBA','HBB','HBM','HBQ')) for g in top20),
}
print("suspect-gene counts in top20:", flag)
# Mouse: substitute 'Mt-', 'Rps'/'Rpl', 'Malat1', 'Hba'/'Hbb' above.

# HVG figure (scanpy's built-in 2-panel mean-vs-dispersion plot)
sc.pl.highly_variable_genes(adata, show=False, save='_hvg.png')
# scanpy writes to figdir as 'highly_variable_genes_hvg.png'
import os; os.rename('highly_variable_genes_hvg.png', 'hvg_plot.png')
```

If the top-20 is dominated by MT or hemoglobin genes, those genes are
driving the HVG selection (and downstream PCA) — fix QC (raise the `pct_mt`
filter; for blood-contaminated tissue, regress out or exclude `^HB` genes
via `adata.var['highly_variable'] &= ~adata.var_names.str.startswith('HB')`)
before continuing. A handful of ribosomal HVGs is usually fine.

### 5. PCA → elbow → choose `n_pcs`

```python
sc.tl.pca(adata, n_comps=50, mask_var='highly_variable')

# Look at the loadings on PC1–PC5 — they should be recognizable biology, not
# just MT/RPS genes (which would mean QC didn't catch dying cells)
loadings = pd.DataFrame(adata.varm['PCs'][:, :5],
                        index=adata.var_names,
                        columns=[f"PC{i+1}" for i in range(5)])
for pc in loadings.columns:
    top_pos = loadings[pc].nlargest(8).index.tolist()
    top_neg = loadings[pc].nsmallest(8).index.tolist()
    print(f"{pc} +: {top_pos}")
    print(f"{pc} -: {top_neg}")

# Per-PC variance ratio (scanpy stores it after sc.tl.pca)
var_ratio = adata.uns['pca']['variance_ratio']                 # per-PC fraction
cum       = np.cumsum(var_ratio)
# Heuristics for context (NOT a decision rule)
n_1pct    = int((var_ratio > 0.01).sum())                       # last PC > 1%
n_05pct   = int((var_ratio > 0.005).sum())                      # last PC > 0.5%
n_knee    = int(np.argmax(np.diff(np.diff(var_ratio))) + 2)     # 2nd-diff knee
N_PCS_CHOSEN = 30                                               # PBMC default

print(f"PCA total variance captured by 50 PCs: {cum[-1]*100:.1f}%")
print(f"variance at PC30: {cum[29]*100:.1f}% (cumulative)")
print(f"heuristics — last PC >1%: {n_1pct} | >0.5%: {n_05pct} | knee: {n_knee}")
print(f"chosen n_pcs = {N_PCS_CHOSEN}")

# Elbow plot — one chart, two curves, same axis (per-PC vs cumulative)
fig, ax = plt.subplots(figsize=(7, 4.8))
x = np.arange(1, len(var_ratio) + 1)
ax.plot(x, var_ratio*100, marker='o', markersize=3, linewidth=1.2,
        color='#1f77b4', label='per-PC variance (%)')
ax.plot(x, cum*100, linestyle='--', linewidth=1.2,
        color='grey40', label='cumulative variance (%)')
ax.axvline(N_PCS_CHOSEN, color='red', linestyle='--', linewidth=0.5)
ax.text(N_PCS_CHOSEN - 0.5, ax.get_ylim()[1]*0.95,
        f"n_pcs = {N_PCS_CHOSEN}", color='red', ha='right')
ax.set(xlabel='principal component', ylabel='% of total variance')
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
ax.set_title(f"PCA elbow — first 50 PCs (HVG matrix)",
             fontsize=12, fontweight='bold', loc='left')
ax.set_title(f"heuristics — >1%:{n_1pct} >0.5%:{n_05pct} knee:{n_knee} | chose {N_PCS_CHOSEN}",
             fontsize=9, color='grey40', loc='right')
ax.legend(loc='upper right', frameon=True, framealpha=0.5)
ax.grid(color='grey90', linewidth=0.3); ax.set_axisbelow(True)
for s in ('top', 'right'): ax.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig('pca_elbow.png', dpi=120,
                                bbox_inches='tight'); plt.close(fig)
```

PC1–PC5 loadings are the most direct check on whether PCA captured biology
or technical artifact: cell-cycle, immune, lineage markers = good; MT or
ribosomal genes dominating = QC missed something, go back to Step 2.

### 6. Neighbor graph → Leiden → UMAP

```python
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=N_PCS_CHOSEN)
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5, flavor='igraph',
             n_iterations=2, directed=False)

n_clusters = adata.obs['leiden'].nunique()
print(f"Leiden res=0.5 → {n_clusters} clusters")
print("cluster sizes:")
print(adata.obs['leiden'].value_counts().sort_index())

sc.pl.umap(adata, color='leiden', legend_loc='on data', legend_fontsize=10,
           legend_fontoutline=2, frameon=True, size=20, alpha=0.6,
           palette='tab20',
           title=f"Leiden res=0.5 · n_pcs={N_PCS_CHOSEN} · "
                 f"n={adata.n_obs} cells · {n_clusters} clusters",
           show=False, save='_clusters.png')
import os; os.rename('umap_clusters.png', 'umap_clusters.png')  # no-op if name matches
```

**If you get one giant cluster + a few tiny ones**, the resolution is too low —
bump to 0.8 or 1.0 and re-run `sc.tl.leiden`. **If you get >30 fragments**,
drop to 0.2–0.3. The cluster-count target is "biologically distinguishable
populations," not a fixed number — for PBMC at this depth, 10–18 clusters at
res=0.5 is typical.

### 7. Cluster markers — table, dotplot, canonical-marker overlay

```python
sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon',
                        pts=True, use_raw=False)

# Tidy marker table (analog of Seurat's FindAllMarkers output) → CSV
def markers_long(adata, key='rank_genes_groups'):
    r = adata.uns[key]
    parts = []
    for g in r['names'].dtype.names:
        parts.append(pd.DataFrame({
            'cluster':         g,
            'gene':            r['names'][g],
            'logfoldchange':   r['logfoldchanges'][g],
            'pvals':           r['pvals'][g],
            'pvals_adj':       r['pvals_adj'][g],
            'pct_in':          r['pts'][g].values if hasattr(r['pts'], 'values') else None,
        }))
    return pd.concat(parts, ignore_index=True)
markers = markers_long(adata)
markers.to_csv('cluster_markers.csv', index=False)
print(f"markers: {len(markers):,} rows for {markers['cluster'].nunique()} clusters")

# Quick scan — top 3 per cluster
top3 = (markers.sort_values('logfoldchange', ascending=False)
                .groupby('cluster').head(3)
                .groupby('cluster')['gene']
                .apply(lambda s: ', '.join(s)).reset_index(name='top3'))
print(top3.to_string(index=False))

# Top-5 marker dotplot, colored by signed log-fold-change (analog of
# Seurat's DotPlot of avg_log2FC; values_to_plot='logfoldchanges' is the key)
RDBU = LinearSegmentedColormap.from_list(
    'rdbu_centered', ['#2166ac', 'grey95', '#b2182b'])
sc.pl.rank_genes_groups_dotplot(
    adata, n_genes=5, values_to_plot='logfoldchanges',
    cmap=RDBU, vcenter=0, vmin=-3, vmax=3,
    swap_axes=False, dendrogram=False,
    show=False, save='_markers.png')
import os; os.rename('dotplot__markers.png', 'markers_dotplot.png')

# Canonical-marker UMAP overlay (FeaturePlot analog)
# Pick a SHORT canonical lineage panel for YOUR tissue; PBMC example below.
canonical = ['CD3D','CD8A','CCR7','NKG7','MS4A1','CD14','FCGR3A','FCER1A','PPBP','MKI67']
canonical = [g for g in canonical if g in adata.var_names]
REDS = LinearSegmentedColormap.from_list('reds_white', ['grey95', '#b2182b'])
sc.pl.umap(adata, color=canonical, ncols=5, cmap=REDS, size=15, alpha=0.6,
           vmin='p1', vmax='p99', frameon=True,
           show=False, save='_canonical.png')
import os; os.rename('umap_canonical.png', 'markers_featureplot.png')
```

The dotplot's color encodes **direction and magnitude** of each gene's
enrichment in its cluster vs the rest — a row of dark-red dots down one
cluster column is a confidently-defined cluster; pale or blue dots mean the
top marker isn't a strong cluster discriminator (often a sign of resolution
that's too fine — see Step 6 guidance).

The canonical-marker overlay is the diagnostic that turns clusters into
*cell types*: if `CD3D` paints one contiguous region, `MS4A1` another,
`CD14` a third, etc., then your clustering is recovering the major lineages.
Patchy or smeared marker maps mean either the marker is wrong for this
tissue, or QC didn't catch ambient RNA contamination.

### 8. Save the processed object

```python
adata.write_h5ad('processed.h5ad')
print(f"saved: processed.h5ad ({os.path.getsize('processed.h5ad')/1e6:.1f} MB)")
print(adata)
```

---

## What this recipe does NOT do

- **Cell-type annotation.** The canonical overlay tells you *which lineage each
  cluster belongs to*; it does not assign labels. For automated annotation use
  `celltypist` or `decoupler`; for marker-based manual annotation use the
  cluster markers + literature for your tissue.
- **Doublet removal.** This recipe filters obvious doublets via the
  high-`n_genes` cutoff, which is coarse. For dedicated doublet detection
  run `scrublet` (Python) or `scDblFinder` (R) on each sample BEFORE Step 2,
  and add the doublet score to `adata.obs` so it lands on the QC violins.
- **Ambient-RNA correction.** If your top markers across multiple unrelated
  clusters all include hemoglobin or `MALAT1`, you have ambient contamination.
  Run `SoupX` (R) or `cellbender` upstream — neither is in scope here.
- **Cell-cycle regression.** Skipped by default; the `MKI67`/`TOP2A` cluster
  is usually informative biology. If cell-cycle is genuinely confounding
  your question (e.g. neural progenitor work), use
  `sc.tl.score_genes_cell_cycle` + `sc.pp.regress_out(['S_score','G2M_score'])`
  before Step 5 — but treat it as a deliberate decision, not a default.
- **Batch correction / integration.** Single-sample only — multiple samples
  belong in `harmony-integration-scanpy` / `scvi-integration` /
  `seurat-integration`.
- **Differential expression across conditions.** `rank_genes_groups` is
  cluster-vs-rest, not condition-vs-condition. For condition DE, pseudobulk
  per cluster and per donor, then `pydeseq2` or `edgeR`.

---

## Common pitfalls (in order of how often they bite)

| Symptom | Cause | Fix |
|---|---|---|
| `pct_counts_mt` is all 0 | Wrong MT prefix for organism | Use `mt-` for mouse, `Mt-` for some others |
| Gene names look like `ENSG00000…` | Read column 1 of `features.tsv.gz` | Use `feature.column=2` / col index 1 |
| Top HVGs are MT or hemoglobin | QC didn't catch dying cells / blood contam | Tighten `pct_mt_high`; exclude `^HB` |
| One giant cluster + tiny fragments | Resolution too low | Bump `resolution` to 0.8–1.2 |
| `sc.pl.*` plots don't appear | `sc.settings.figdir` not set to `.` | First line of the run: `sc.settings.figdir = '.'` |
| `rank_genes_groups` returns nonsense | `sc.pp.scale` was run, X is z-scores | Don't scale X in place; PCA can work on log-norm |
| UMAP shows obvious batch separation | Concatenated multiple samples here | Use an integration recipe, not this one |
| Lost >30% of cells in QC | Thresholds too strict OR real quality issue | Re-read quantile table; check tissue defaults |

