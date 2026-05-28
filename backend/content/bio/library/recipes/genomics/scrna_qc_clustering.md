---
name: scrna-qc-clustering
description: scanpy QC + first-pass clustering for single-cell RNA-seq
when_to_use: scRNA-seq dataset, fresh from CellRanger or similar; want to see clusters / UMAP before any biology
requires_tools: [run_python]
capabilities_needed: [scanpy, leidenalg]
keywords: [single cell, scRNA-seq, clustering, UMAP, leiden, marker genes, cell QC, filtering]
produces: [qc_summary.csv, umap.png, leiden_markers.csv]
resource_profile: small-medium  (~30s for 10–50k cells)
---

# scRNA-seq QC + first-pass clustering

Compact scanpy pipeline. Halt and present_plan before running this when
the dataset is unfamiliar — defaults below are sensible but the user
may want different thresholds.

## Procedure

1. `sc.read_*` the data (depends on input — 10x mtx, h5ad, raw counts).
2. `sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], inplace=True)`.
3. Filter cells: `n_genes_by_counts >= 200`, `pct_counts_mt < 20`.
4. Filter genes: `min_cells=3`.
5. `sc.pp.normalize_total(adata, target_sum=1e4)` then `sc.pp.log1p(adata)`.
6. `sc.pp.highly_variable_genes(adata, n_top_genes=2000)`.
7. `sc.pp.scale(adata, max_value=10)`.
8. `sc.tl.pca(adata, n_comps=50)`.
9. `sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)`.
10. `sc.tl.umap(adata)`.
11. `sc.tl.leiden(adata, resolution=0.5)`.
12. `sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon')`.

## Outputs

- `qc_summary.csv`: per-cell n_genes, total_counts, pct_mt before & after filtering
- `umap.png`: UMAP coloured by leiden cluster
- `leiden_markers.csv`: top markers per cluster (long form)

## Common adjustments

- **Mitochondrial fraction** — 20 % is a fresh-tissue default. For
  frozen samples 10 % is more typical; ask first.
- **Resolution** — 0.5 gives a moderate number of clusters; 0.2–0.3
  for coarser, 0.8–1.2 for finer.
- **HVG count** — drop to 1000 if memory-constrained.
