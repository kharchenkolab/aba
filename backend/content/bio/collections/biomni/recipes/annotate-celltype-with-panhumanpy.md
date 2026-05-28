---
name: annotate-celltype-with-panhumanpy
description: Hierarchical cell type annotation of scRNA-seq data using the Azimuth Neural Network via panhumanpy
when_to_use: Annotate human single-cell RNA-seq data with hierarchical cell type labels and confidence scores using the Satija Lab pan-human reference
requires_tools: [run_python]
capabilities_needed: [panhumanpy, scanpy, anndata, numpy]
keywords: [cell type annotation, scRNA-seq, Azimuth, pan-human, hierarchical labels, UMAP, single-cell]
produces: [annotated_cell_metadata.csv, ann_embeddings.npy, ann_umap.npy, annotated_obj.h5ad]
domain: genomics
source: biomni:tool/genomics.py::annotate_celltype_with_panhumanpy
---
# Annotate Cell Types with Panhumanpy (Azimuth Neural Network)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load h5ad with `scanpy.read_h5ad`; confirm n_obs and n_vars.
2. Instantiate `panhumanpy.AzimuthNN(adata)` — pass `feature_names_col` if gene names live in a var column rather than the index; otherwise fall back to the index.
3. Retrieve per-cell metadata: `cell_metadata = azimuth.cells_meta`.
4. Optionally generate ANN embeddings: `embeddings = azimuth.azimuth_embed()` followed by `azimuth.azimuth_umap()` to produce UMAP coordinates.
5. Optionally refine labels for consistent granularity: `azimuth.azimuth_refine()`; refined columns are prefixed `azimuth_` in `cells_meta`.
6. Save outputs: `cell_metadata.to_csv(...)`, `np.save(...)` for embeddings and UMAP, `azimuth.pack_adata(save_path=...)` for the annotated h5ad.

## Key decisions
- `feature_names_col`: if the var index is already gene symbols, pass `None`; if a separate column holds them, pass its name.
- `refine=True` enforces consistent label granularity across hierarchy levels — recommended for downstream analysis.
- `umap=True` adds ANN-space UMAP; skip to save time when only labels are needed.

## Caveats
- Performance is not guaranteed for non-human or diseased cells.
- panhumanpy may require a separate conda environment; install via `pip install git+https://github.com/satijalab/panhumanpy.git`.
- Large datasets need substantial RAM for ANN embedding.

## In ABA
Implement with `run_python`; `ensure_capability("panhumanpy", "scanpy", "numpy")`. Original impl: `biomni:tool/genomics.py::annotate_celltype_with_panhumanpy` — lift to lakeFS later.
