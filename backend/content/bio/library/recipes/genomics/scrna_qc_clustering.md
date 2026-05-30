---
name: scrna-qc-clustering
description: Standard scanpy processing for ONE scRNA-seq sample — QC, filtering, normalization, highly variable genes, PCA, Leiden clustering, UMAP and cluster markers. The quick end-to-end first pass on a count matrix.
when_to_use: You have a single scRNA-seq sample (10x mtx / h5ad / raw count matrix) and want to process it with scanpy — basic/standard processing and a first-pass clustering (QC → normalize → cluster → UMAP → markers) before any biology. For principled MAD-based QC instead, see bp-quality-control; for FASTQ→counts see bp-raw-data-processing.
avoid_when: "Multiple samples/donors you intend to INTEGRATE jointly (use harmony/scvi/seurat-integration — do not concat-then-cluster); bulk RNA-seq; only a gene list with no count matrix; cross-condition DE across donors (needs pseudobulk + a DE recipe, not this single-sample clustering)."
requires_tools: [run_python]
capabilities_needed: [scanpy, leidenalg]
keywords: [scanpy, process, processing, preprocess, preprocessing, basic processing, standard scanpy pipeline, scanpy workflow, single cell, scRNA-seq, normalize, normalization, log1p, clustering, leiden, UMAP, PCA, highly variable genes, marker genes, cell QC, quality control, filtering, first-pass, end-to-end]
produces: [violin_qc.png, scatter_qc.png, hvg_pca.png, umap_leiden.png, dotplot markers png, leiden_markers.csv, processed.h5ad]
resource_profile: small-medium  (~30s for 10–50k cells)
---

# scRNA-seq QC + first-pass clustering (scanpy)

The one canonical compact single-cell flow: load → QC → filter → normalize → HVG
→ PCA → neighbors/UMAP/leiden → markers, for **one sample**. Halt and
`present_plan` before running on an unfamiliar dataset — the defaults are
sensible but the QC thresholds and the clustering resolution are dataset-dependent.

## Multiple samples: keep them SEPARATE — do NOT naively concatenate
Several samples/donors/runs are **multiple datasets, not one matrix**. `sc.concat`-ing
raw counts and clustering the result confounds batch with biology (the clusters
just separate by sample). "Register them **together**" = ONE dataset entity
spanning the per-sample files, NOT a merged matrix. Combine samples only as the
explicit first step of a **batch-aware integration** (`harmony-integration`,
`scvi-integration`, `conos-integration`) — never a concatenate-then-cluster
shortcut. One sample = this recipe; two+ jointly = an integration recipe.

**Honor the requested scope — don't upsell integration.** This guardrail stops
*naive concatenation*; it is NOT a reason to push integration. If the user asks
to process one sample (e.g. "the second sample"), run this recipe on exactly that
sample and stop. Other samples existing is not a cue to propose batch correction —
integration is a separate, explicit request. Make the plan about the sample asked
for, nothing more.

## Plotting rules (so the figures actually show, and stay legible)
- **First line of the run:** `sc.settings.figdir = '.'`. Otherwise `sc.pl.*(save=…)`
  writes to a `figures/` subdir the harness does NOT harvest and the plots never
  appear. Hand-built figures: `plt.savefig('name.png', dpi=120)` into the cwd.
- **At most ~3 panels per figure.** Three QC violins side-by-side = fine; a dense
  8-panel grid with text = too much. One idea per figure.
- We deliberately **do not `sc.pp.scale`** — PCA runs on log-normalized HVGs, which
  keeps `adata.X` log-normalized so `rank_genes_groups` and gene overlays read real
  expression. (Scaling X in place silently corrupts marker detection.)

## Procedure
```python
import scanpy as sc, matplotlib.pyplot as plt
sc.settings.figdir = '.'        # harvested cwd, not figures/

# 1. Load. Files live under DATA_DIR (get the path from list_data_files; do NOT
#    guess WORK_DIR). Two common layouts — pick the one that matches:
import os, pandas as pd
D = os.environ['DATA_DIR']
#  (a) Standard CellRanger DIR (barcodes/features/matrix.mtx[.gz] inside it):
#      adata = sc.read_10x_mtx(f"{D}/<sample_dir>", var_names='gene_symbols')
#  (b) GEO LOOSE, GSM-PREFIXED triplet (…matrix.mtx.gz / …barcodes.tsv.gz /
#      …features.tsv.gz all sitting in one dir) — read_10x_mtx will NOT find these
#      (non-standard names), so read the three parts EXPLICITLY. This is the usual
#      GEO supplementary layout and the #1 source of "lost gene names" flailing:
pre = "<GSM..._sample_prefix>"                                  # the shared file prefix
adata = sc.read_mtx(f"{D}/{pre}.matrix.mtx.gz").T               # mtx is genes×cells → transpose
adata.obs_names = pd.read_csv(f"{D}/{pre}.barcodes.tsv.gz", header=None)[0].values
adata.var_names = pd.read_csv(f"{D}/{pre}.features.tsv.gz", header=None, sep='\t')[1].values  # col 2 = symbols
adata.var_names_make_unique()                                  # do NOT skip — duplicate symbols are common

# 2. QC metrics (flag mito genes first)
adata.var['mt'] = adata.var_names.str.upper().str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)

# 3. Pre-filter QC — three distributions side by side, plus a counts/genes scatter
sc.pl.violin(adata, ['n_genes_by_counts', 'total_counts', 'pct_counts_mt'],
             jitter=0.4, multi_panel=True, show=False, save='_qc.png')      # violin_qc.png
sc.pl.scatter(adata, x='total_counts', y='n_genes_by_counts', color='pct_counts_mt',
              show=False, save='_qc.png')                                   # scatter_qc.png

# 4. Filter (human PBMC defaults — see the tissue table for others)
n0 = adata.n_obs
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata = adata[adata.obs.pct_counts_mt < 20].copy()
print(f'cells: {n0} -> {adata.n_obs}')

# 5. Normalize + log1p
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)

# 6. Highly variable genes — default flavor expects LOG data (do NOT use
#    flavor='seurat_v3' here; it needs raw counts)
sc.pp.highly_variable_genes(adata, n_top_genes=2000)

# 7. PCA on the HVGs
sc.pp.pca(adata, n_comps=50, use_highly_variable=True)

# 8. HVG selection + PCA top-components, paired in one 2-panel figure
fig, ax = plt.subplots(1, 2, figsize=(9, 3.8))
hv = adata.var['highly_variable'].values
ax[0].scatter(adata.var['means'][~hv], adata.var['dispersions_norm'][~hv], s=3, c='#cbd5e1', label='other')
ax[0].scatter(adata.var['means'][hv],  adata.var['dispersions_norm'][hv],  s=3, c='#0f766e', label='HVG')
ax[0].set(xscale='log', xlabel='mean expression', ylabel='normalized dispersion',
          title=f'Highly variable genes (n={int(hv.sum())})')
ax[0].legend(frameon=False, markerscale=2)
vr = adata.uns['pca']['variance_ratio']
ax[1].plot(range(1, len(vr) + 1), vr, 'o-', ms=3, c='#0f766e')
ax[1].set(xlabel='principal component', ylabel='variance ratio', title='PCA — top components')
fig.tight_layout(); fig.savefig('hvg_pca.png', dpi=120); plt.close(fig)

# 9. Neighbors + UMAP + Leiden — ONE resolution (no multi-resolution sweep)
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5)

# 10. UMAP by cluster. legend_loc='on data' prints each cluster label ON the
#     plot — the robust way to show the cluster legend (a right-margin legend
#     with many clusters gets clipped when the figure is saved).
sc.pl.umap(adata, color='leiden', legend_loc='on data', frameon=False,
           title='Leiden clusters', show=False, save='_leiden.png')        # umap_leiden.png

# 11. Markers per cluster — table + a COMPACT dotplot (not a dense heatmap)
sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon')
sc.get.rank_genes_groups_df(adata, group=None).to_csv('leiden_markers.csv', index=False)
sc.pl.rank_genes_groups_dotplot(adata, n_genes=5, show=False, save='_markers.png')

# 12. Save the processed object for downstream work
adata.write('processed.h5ad')
```

## Outputs
- `violin_qc.png` — n_genes / total_counts / pct_mt distributions (3 panels)
- `scatter_qc.png` — counts vs genes, coloured by mito %
- `hvg_pca.png` — HVG selection + PCA variance per component, side by side
- `umap_leiden.png` — UMAP coloured by Leiden cluster (labels on the clusters)
- dotplot of top markers (`dotplot_*.png`) + `leiden_markers.csv`
- `processed.h5ad` — the processed AnnData, ready for annotation / DE / etc.

## Common adjustments
- **Mitochondrial %** — 20 % is a fresh-tissue default; frozen tissue → ~10 %. Ask.
- **Resolution** — one value (0.5 = moderate); 0.2–0.3 coarser, 0.8–1.2 finer.
  Pick one for the first pass; don't sweep resolutions unless the user asks.
- **HVG count** — 2000 is standard; drop to 1000 if memory-constrained.

## Suggested thresholds by tissue
- Human PBMC: pct_mt < 20 %, n_genes ∈ [200, 2500]
- Mouse PBMC: pct_mt < 10 %, n_genes ∈ [200, 4000]
- Tumor (heterogeneous): pct_mt < 25 %, broader n_genes range

## "What if we tighten QC?"
That's a scenario — use the `create_scenario_variant` flow: take the producing
code, change the threshold, re-run. The new artifacts edge back to the baseline
(`variantOf`) and the Compare toggle flips between the two.
