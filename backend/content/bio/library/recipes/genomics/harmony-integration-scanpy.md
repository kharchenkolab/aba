---
name: harmony-integration-scanpy
description: Batch/sample integration of scRNA-seq with Python/scanpy + Harmony (harmonypy) — load & concat multiple samples, QC→normalize→HVG→PCA, then the one-line sc.external.pp.harmony_integrate over a batch covariate, and cluster/UMAP on the harmony reduction with before/after mixing plots.
when_to_use: Two or more scRNA-seq samples/conditions/donors (10x lanes, stim vs ctrl, batches) in a Python/scanpy session whose batch effect is visible in a plain PCA/UMAP, and you want a fast, linear batch correction before clustering/annotation. Use THIS (Python/scanpy + harmonypy) when the session is Python-based or the user asks for Harmony in scanpy. For an R/Seurat session use harmony-integration (RunHarmony). For an embedding-only step when PCA already exists, see create-harmony-embeddings-scrna. For a deep-generative alternative see scvi-integration. For a single clean sample no integration is needed — see scrna-qc-clustering.
requires_tools: [run_python]
capabilities_needed: [scanpy, harmonypy, leidenalg, anndata]
keywords: [harmony, harmonypy, sc.external.pp.harmony_integrate, batch correction, batch integration, sample integration, scanpy, X_pca_harmony, multi-sample, scRNA-seq, donor effect, UMAP, leiden, single cell, integrate samples, batch effect, concat, batch key, Z_corr]
produces: [umap_before_harmony.png, umap_after_harmony.png, umap_leiden.png, leiden_clusters.csv, integrated.h5ad]
domain: genomics
resource_profile: small-medium (~1-2 min for a few 10x samples, ~30-80k cells)
source: "scanpy external API (sc.external.pp.harmony_integrate, backed by harmonypy / Korsunsky 2019); Python counterpart of the R/Seurat harmony-integration recipe"
---

# scRNA-seq batch integration with Python/scanpy + Harmony (harmonypy)

Harmony is a fast, linear batch-correction method: it iteratively soft-clusters
cells in PCA space and learns a correction that pulls the same cell types from
different batches together while **leaving the expression matrix untouched** — it
corrects an *embedding* (PCA-space), written to `adata.obsm['X_pca_harmony']`.
Then run every downstream step (`sc.pp.neighbors`, `sc.tl.umap`, `sc.tl.leiden`)
on `use_rep='X_pca_harmony'` instead of the default PCA.

> **⚠ With harmonypy ≥2.0 (the version `ensure_capability` installs), the scanpy
> wrapper `sc.external.pp.harmony_integrate` is BROKEN** — harmonypy 2.0 changed
> `Z_corr`'s orientation, and the wrapper writes it to `obsm` without transposing,
> so AnnData raises `ValueError: Value had shape (n_pcs, n_cells) while it should
> have had (n_cells, n_pcs)`. So call `run_harmony` yourself and store the
> transpose — it's TWO lines and there's no attribute guessing if you use exactly
> this (the corrected coords are `ho.Z_corr.T` — NOT `Z_corrected`):
> ```python
> import harmonypy
> ho = harmonypy.run_harmony(adata.obsm['X_pca'], adata.obs, ['sample'])
> adata.obsm['X_pca_harmony'] = ho.Z_corr.T   # Z_corr is (n_pcs × n_cells) → transpose
> ```
> (If you're on harmonypy <2.0, `sc.external.pp.harmony_integrate(adata, key='sample')`
> also works and does this for you — but don't rely on it under 2.x.)

**Provision:** `ensure_capability('scanpy')`, `ensure_capability('harmonypy')`
(pip package `harmonypy`; the wrapper imports it lazily and errors with "Please
install `harmonypy`" if missing), plus `leidenalg` for clustering and `anndata`.

## The choices that DEFINE the integration — surface them with present_plan
Halt and walk the user through these before running; this is exactly where an
advisor adds value, because over-integration silently erases real biology.
1. **What to integrate over (`key`)** — the `adata.obs` column you want the batch
   effect removed *for*. Integrate over the **technical** nuisance (sample, donor,
   lane, batch, 10x run), **not** the biological variable you care about. If
   `stim`/`ctrl` IS the question, integrating over it washes out the signal you're
   studying. Pass a list `['donor', 'lane']` to correct several at once.
2. **Number of PCs** — `n_comps` in `sc.pp.pca` flows through Harmony and the
   neighbor graph (`n_pcs` in `sc.pp.neighbors`); keep them consistent.
3. **Clustering resolution** — `sc.tl.leiden(resolution=...)` sets cluster count.

Never integrate over the variable you intend to test — you'd remove the effect
and then "discover" there is none.

## Plotting rules (so the figures actually show)
- **First line of the run:** `sc.settings.figdir = '.'`. Otherwise
  `sc.pl.*(save=…)` writes to a `figures/` subdir the harness does NOT harvest
  and the plots never appear. Hand-built figures: `plt.savefig('name.png',
  dpi=120)` into the cwd.
- **At most ~2-3 panels per figure**, one idea per figure.
- **We deliberately do not `sc.pp.scale`** — PCA runs on log-normalized HVGs, so
  `adata.X` stays log-normalized and `rank_genes_groups`/gene overlays read real
  expression.

## Procedure

```python
import scanpy as sc, anndata as ad, pandas as pd, os
import matplotlib.pyplot as plt
sc.settings.figdir = '.'            # harvested cwd, not figures/

# 1. Load each sample and CONCAT with a batch key. Files live under DATA_DIR
#    (get the path from list_data_files; do NOT guess WORK_DIR).
D = os.environ['DATA_DIR']
#  (a) Standard CellRanger DIR per sample (barcodes/features/matrix.mtx[.gz] inside):
#      a = sc.read_10x_mtx(f"{D}/{sample_dir}", var_names='gene_symbols')
#  (b) GEO LOOSE, GSM-PREFIXED triplets (…matrix.mtx.gz / …barcodes.tsv.gz /
#      …features.tsv.gz all in one dir) — read_10x_mtx will NOT find these
#      (non-standard names), so read the three parts EXPLICITLY per sample:
def load_geo_10x(prefix):
    a = sc.read_mtx(f"{D}/{prefix}.matrix.mtx.gz").T          # mtx is genes×cells → transpose
    a.obs_names = pd.read_csv(f"{D}/{prefix}.barcodes.tsv.gz", header=None)[0].values
    a.var_names = pd.read_csv(f"{D}/{prefix}.features.tsv.gz", header=None, sep='\t')[1].values  # col 2 = symbols
    a.var_names_make_unique()                                 # do NOT skip — duplicate symbols are common
    return a

prefixes    = ["GSM..._s1", "GSM..._s2", "GSM..._s3"]         # the per-sample file prefixes
sample_names = ["s1", "s2", "s3"]                             # human-readable labels
# concat tags each cell's origin in obs['sample']; index_unique keeps barcodes distinct
adata = ad.concat([load_geo_10x(p) for p in prefixes],
                  label='sample', keys=sample_names, index_unique='-')

# 2. QC metrics + light filtering (PBMC defaults — see scrna-qc-clustering for the
#    per-tissue thresholds and the full QC plots if you want them).
adata.var['mt'] = adata.var_names.str.upper().str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata = adata[adata.obs.pct_counts_mt < 20].copy()

# 3. Normalize → log1p → HVG → PCA (the standard pre-integration pipeline).
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, n_top_genes=2000)         # default flavor expects LOG data
sc.pp.pca(adata, n_comps=50, use_highly_variable=True)       # writes obsm['X_pca']

# 4. BEFORE: UMAP on the raw PCA, coloured by sample — shows the batch effect
#    you are about to correct (samples should separate / form their own islands).
sc.pp.neighbors(adata, n_pcs=30, n_neighbors=15)
sc.tl.umap(adata)
adata.obsm['X_umap_pca'] = adata.obsm['X_umap'].copy()       # stash the pre-Harmony UMAP
sc.pl.umap(adata, color='sample', frameon=False, title='Before Harmony (PCA)',
           show=False, save='_before_harmony.png')           # umap_before_harmony.png

# 5. INTEGRATE — run Harmony on the PCA and store the corrected embedding.
#    (The scanpy wrapper sc.external.pp.harmony_integrate is broken on harmonypy>=2.0
#     — ValueError shape mismatch — so call run_harmony directly and transpose Z_corr.)
import harmonypy
ho = harmonypy.run_harmony(adata.obsm['X_pca'], adata.obs, ['sample'])  # 'sample' = batch col
adata.obsm['X_pca_harmony'] = ho.Z_corr.T                    # (n_pcs × n_cells) → (cells × pcs)

# 6. Downstream on the HARMONY embedding: re-run neighbors with use_rep, then UMAP + leiden.
sc.pp.neighbors(adata, use_rep='X_pca_harmony', n_neighbors=15)
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5)

# 7. AFTER: same UMAP space coloured by sample (want: well MIXED) and by leiden
#    (want: clean clusters). This is the real test that integration worked.
sc.pl.umap(adata, color='sample', frameon=False, title='After Harmony (X_pca_harmony)',
           show=False, save='_after_harmony.png')            # umap_after_harmony.png
sc.pl.umap(adata, color='leiden', legend_loc='on data', frameon=False,
           title='Leiden clusters (integrated)', show=False, save='_leiden.png')  # umap_leiden.png

# 8. Persist.
adata.obs[['sample', 'leiden']].to_csv('leiden_clusters.csv')
adata.write('integrated.h5ad')
```

## Outputs
- `umap_before_harmony.png` — UMAP on raw PCA coloured by sample (the batch effect)
- `umap_after_harmony.png` — UMAP on `X_pca_harmony` coloured by sample (mixing)
- `umap_leiden.png` — integrated UMAP coloured by Leiden cluster (labels on data)
- `leiden_clusters.csv` — per-cell sample + cluster assignment
- `integrated.h5ad` — the integrated AnnData (`obsm['X_pca_harmony']` + clusters), ready for annotation / DE / etc.

## Assess batch mixing — did it work?
Integration succeeds when cells from different samples **interleave** within
shared cell types yet **distinct cell types stay separate**. Compare
`umap_before_harmony.png` (samples in separate islands → there was a batch effect
to fix) against `umap_after_harmony.png` (samples mixed). Read it: if samples
still form separate islands per cell type, integration is too weak; if
biologically distinct types collapsed together, it's too aggressive — tune
`theta` (see below).

## Tuning knobs (passed straight through to harmonypy)
Extra kwargs to `sc.external.pp.harmony_integrate` are forwarded to
`harmonypy.run_harmony`. Defaults are good; reach for these only when the mixing
diagnostic demands it.
- **`theta`** — diversity penalty per corrected covariate (default `2`). Higher =
  more aggressive mixing; lower = gentler. The first dial to turn:
  `sc.external.pp.harmony_integrate(adata, key='sample', theta=3)`.
- **`max_iter_harmony`** — iteration cap (default `10`).
- **`adjusted_basis`** — change the output obsm key (default `'X_pca_harmony'`)
  only if you need to keep multiple corrections side by side.

## Caveats to surface
- **Harmony corrects an embedding, not expression** — use `use_rep='X_pca_harmony'`
  for neighbors/UMAP/leiden; run DE on `adata.X` (log-normalized expression) via
  `rank_genes_groups`, never on the harmony coordinates. (See the scRNA DE note:
  bulk DE tools are not for per-cell scRNA DE.)
- **Integrate the nuisance, not the signal** — never set `key` to the biological
  variable you want to test.
- **PCA must exist first** — the wrapper reads `obsm['X_pca']`; run `sc.pp.pca`
  before calling it.
- **Use the wrapper** — `sc.external.pp.harmony_integrate`, not raw
  `harmonypy.run_harmony` + manual `Z_corr.T` extraction.
- **Honor the requested scope** — don't tack on annotation/reports the user
  didn't ask for; stop at the integrated object + mixing plots.

## Cross-links
- **harmony-integration** — the R/Seurat counterpart (`RunHarmony`); prefer it
  when the session is R-based or the user names Seurat. Same method, same tuning
  knobs (theta/sigma/lambda).
- **create-harmony-embeddings-scrna** — the embedding-only variant when PCA is
  already computed and you just want the corrected embedding (also leads with
  `sc.external.pp.harmony_integrate`).
- **scrna-qc-clustering** — the single-sample scanpy QC→clustering baseline; run
  it per sample first to confirm a batch effect exists.
- **scvi-integration** — deep-generative (scVI) batch integration; prefer it over
  Harmony for very large atlases, complex/nested batch structure, or when a
  trained model is needed for label transfer (scANVI) or scVI-based DE.
- **conos-integration** — joint-graph multi-sample integration in R.

## In ABA
`ensure_capability('scanpy')`, `ensure_capability('harmonypy')`,
`ensure_capability('leidenalg')`, then run every step in `run_python`; write
`integrated.h5ad` so a later `run_python` resumes from it. Prefer Python/scanpy +
harmonypy when the session is Python-native; for an R session use
harmony-integration, and for a deeper model see scvi-integration.
