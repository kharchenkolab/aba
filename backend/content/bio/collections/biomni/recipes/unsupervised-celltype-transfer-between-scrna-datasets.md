---
name: unsupervised-celltype-transfer-between-scrna-datasets
description: Transfer cell-type labels from an annotated reference scRNA-seq dataset to an unannotated query dataset using popV ensemble methods
when_to_use: Have an annotated reference h5ad and an unannotated query h5ad; want label transfer without requiring manual clustering of the query
requires_tools: [run_python]
capabilities_needed: [scanpy, popv]
keywords: [cell type transfer, label transfer, popV, scVI, SCANVI, KNN, scanorama, harmony, celltypist, scRNA-seq, single cell]
produces: [popv_output/predictions.csv with per-cell predicted labels, trained model artifacts in output_folder]
domain: genomics
source: biomni:tool/genomics.py::unsupervised_celltype_transfer_between_scRNA_datasets
---
# Unsupervised cell-type transfer between scRNA-seq datasets (popV ensemble)

Distilled from a biomni implementation. In ABA, implement with the libraries
below — not biomni.

## Approach
1. Load reference (annotated) and query (unannotated) AnnData files via `sc.read_h5ad`.
2. Copy raw counts to `.layers["counts"]` on both objects — required for scVI/SCANVI-based classifiers.
3. Configure popV: set `popv.settings.n_jobs`; create the output folder.
4. Preprocess the query against the reference with `popv.preprocessing.Process_Query(query_adata, ref_adata, ref_labels_key=..., query_batch_key=..., ref_batch_key=..., save_path_trained_models=output_folder, cl_obo_folder=False, prediction_mode="retrain")`. This aligns gene spaces and prepares joint embeddings.
5. Select annotation methods from the flag set: `CELLTYPIST`, `KNN_BBKNN`, `KNN_HARMONY`, `KNN_SCANORAMA`, `KNN_SCVI`, `ONCLASS`, `Random_Forest`, `SCANVI_POPV` (default True), `Support_Vector`, `XGboost`.
6. Run `popv.annotation.annotate_data(adata, methods=selected_methods, save_path=f"{output_folder}/popv_output")`. Predictions are written to `predictions.csv`.

## Key decisions
- Default method is `SCANVI_POPV` only (the others default to False); caller enables additional methods explicitly.
- `prediction_mode="retrain"` — models are always retrained on the reference; no cached model reuse by default.
- `cl_obo_folder=False` — Cell Ontology OBO file lookup disabled; popV uses its built-in vocabulary.
- `n_samples_per_label=10` parameter is accepted but passed to `popv.settings` indirectly (verify against popV version in use).

## Caveats
- Reference dataset must have raw counts available (X is treated as counts; a pre-normalized reference will produce incorrect SCANVI embeddings).
- Query and reference must share a common gene space or popV's preprocessing will fail; pre-filter to HVGs before calling.
- Training all 9 methods is computationally expensive; enable only the methods needed.
- `n_jobs` parallelism is global state (`popv.settings.n_jobs`); avoid concurrent calls in the same process.

## In ABA
Implement with `scanpy` + `popv`. `ensure_capability(scanpy, popv)`. The returned composition table from `predictions.csv` can be passed as the `composition` argument to the `annotate-celltype-scrna` recipe for LLM-assisted re-annotation.
