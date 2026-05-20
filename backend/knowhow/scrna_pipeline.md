# Compact scRNA-seq pipeline (scanpy)

Reference the Guide consults when given a single-cell RNA-seq task. Captures the canonical pbmc3k-style flow in a self-contained block.

## Stages

1. **Load**
   ```python
   import scanpy as sc
   adata = sc.read_10x_mtx(PATH, var_names='gene_symbols')  # or sc.read_h5ad(...)
   ```

2. **QC**
   ```python
   adata.var['mt'] = adata.var_names.str.upper().str.startswith('MT-')
   sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
   # Reasonable defaults for human peripheral blood:
   #   n_genes_by_counts  >= 200
   #   pct_counts_mt      <  20.0
   ```

3. **Filter**
   ```python
   sc.pp.filter_cells(adata, min_genes=200)
   sc.pp.filter_genes(adata, min_cells=3)
   adata = adata[adata.obs.pct_counts_mt < 20].copy()
   ```

4. **Normalize + log1p**
   ```python
   sc.pp.normalize_total(adata, target_sum=1e4)
   sc.pp.log1p(adata)
   ```

5. **Highly variable genes**
   ```python
   sc.pp.highly_variable_genes(adata, n_top_genes=2000, flavor='seurat_v3')
   ```

6. **PCA + neighbors + UMAP**
   ```python
   sc.pp.pca(adata, n_comps=50, use_highly_variable=True)
   sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)
   sc.tl.umap(adata)
   ```

7. **Leiden clustering**
   ```python
   sc.tl.leiden(adata, resolution=0.5)
   ```

8. **Marker genes per cluster**
   ```python
   sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon')
   ```

## Plotting

Each plot should land as a separate PNG (so the harness registers it as a distinct figure entity). Always call `plt.tight_layout()` and use `dpi=120`.

- QC violins: `sc.pl.violin(adata, ['n_genes_by_counts','total_counts','pct_counts_mt'], jitter=0.4, multi_panel=True, save='_qc_violin.png')`
- QC scatter: `sc.pl.scatter(adata, x='total_counts', y='n_genes_by_counts', color='pct_counts_mt', save='_qc_scatter.png')`
- UMAP overlay: `sc.pl.umap(adata, color='leiden', legend_loc='on data', save='_umap_leiden.png')`
- Top marker heatmap: `sc.pl.rank_genes_groups_heatmap(adata, n_genes=4, save='_markers.png')`

Note: scanpy's `sc.pl.*` functions with `save=...` write to `figures/scanpy_*.png` by default. The harness expects `.png` files in the script's cwd, so either set `sc.settings.figdir = '.'` before plotting, or copy/move the files explicitly.

## Suggested thresholds for common tissues

- **Human PBMC**: pct_counts_mt < 20%, n_genes ∈ [200, 2500]
- **Mouse PBMC**: pct_counts_mt < 10%, n_genes ∈ [200, 4000]
- **Tumor (heterogeneous)**: pct_counts_mt < 25%, broader n_genes range

## When the user says "what if we tighten QC"

That's a scenario. Use `create_scenario_variant` flow: take the producing code, modify the threshold, re-run. The new artifacts will edge back to the baseline with `variantOf`, and the Compare toggle will let the user flip between the two.
