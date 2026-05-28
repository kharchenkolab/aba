---
name: map-to-ima-interpret-scrna
description: Map scRNA-seq cells to the Integrated Megascale Atlas (IMA) reference using UCE embeddings and kNN voting
when_to_use: Assign coarse cell types to query cells by nearest-neighbor matching against the IMA reference dataset in UCE embedding space
requires_tools: [run_python]
capabilities_needed: [scikit-learn, scanpy, anndata, numpy, pandas]
keywords: [cell type mapping, IMA, Integrated Megascale Atlas, UCE, kNN, scRNA-seq, cell annotation]
produces: [adata_with_mapped_celltypes.h5ad with obs mapped_cell_type and mapping_confidence]
domain: genomics
source: biomni:tool/genomics.py::map_to_ima_interpret_scRNA
---
# Map Cells to IMA Reference and Interpret Cell Types

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load query AnnData: `sc.read_h5ad(f"{data_dir}/{adata_filename}")`; confirm `adata.obsm["X_uce"]` exists (run `get-uce-embeddings-scrna` first).
2. Load IMA reference: `sc.read_h5ad(f"{data_dir}/uce_10000_per_dataset_33l_8ep_coarse_ct.h5ad")`.
3. Validate embedding dimensions match between query `obsm["X_uce"]` and reference `X`.
4. Fit kNN on reference: `NearestNeighbors(n_neighbors=n_neighbors, metric=metric).fit(IMA_adata.X)`.
5. Query: `distances, indices = nn.kneighbors(adata.obsm["X_uce"])`.
6. Extract neighbor cell types from `IMA_adata.obs["coarse_cell_type_yanay"]`.
7. If `n_neighbors > 1`, apply majority voting via `np.apply_along_axis(custom_mode, 1, ...)`.
8. Assign `adata.obs["mapped_cell_type"]` and `adata.obs["mapping_confidence"] = 1 / (1 + distances.mean(axis=1))`.
9. Save: `adata.write_h5ad(output, compression="gzip")`.

## Key decisions
- `n_neighbors` (default 3): higher values smooth noisy mappings via majority vote.
- `metric` (default "euclidean"): cosine distance is an alternative for normalized embeddings.
- Confidence score is an inverse-distance proxy; low scores flag ambiguous cells.

## Caveats
- IMA reference h5ad must be pre-downloaded; it is a large file (~several GB).
- Only coarse cell types are available in the reference (`coarse_cell_type_yanay` column).

## In ABA
Implement with `run_python`; `ensure_capability("scikit-learn", "scanpy", "numpy")`. Original impl: `biomni:tool/genomics.py::map_to_ima_interpret_scRNA` — lift to lakeFS later.
