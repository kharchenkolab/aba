---
name: create-harmony-embeddings-scrna
description: Batch-correct scRNA-seq PCA embeddings with Harmony to produce integrated low-dimensional representations
when_to_use: AnnData has PCA computed (.obsm["X_pca"]) and a batch covariate in .obs; want batch-corrected embeddings for downstream clustering or visualization
requires_tools: [run_python]
capabilities_needed: [scanpy, harmony-pytorch]
keywords: [Harmony, batch correction, integration, PCA, embeddings, scRNA-seq, single cell, batch effect]
produces: ["X_harmony embedding added to .obsm", "harmony_emb_data.h5ad saved to data_dir"]
domain: genomics
source: biomni:tool/genomics.py::create_harmony_embeddings_scRNA
---
# Create Harmony batch-corrected embeddings for scRNA-seq

Distilled from a biomni implementation. In ABA, implement with the libraries
below — not biomni.

## Approach
1. Load AnnData: `sc.read_h5ad(f"{data_dir}/{adata_filename}")`.
2. Run Harmony integration: `harmonize(adata.obsm["X_pca"], adata.obs, batch_key=batch_key)` from the `harmony` package (harmony-pytorch). The result is a corrected embedding matrix of the same shape as `X_pca`.
3. Store result: `adata.obsm["X_harmony"] = <corrected matrix>`.
4. Save updated AnnData to `{data_dir}/harmony_emb_data.h5ad`.

## Key decisions
- Uses `harmony-pytorch` (`from harmony import harmonize`), not `harmonypy`; verify the installed package matches this import.
- Input embedding must already exist at `.obsm["X_pca"]`; run `sc.pp.pca` first if not present.
- No custom Harmony hyperparameters are set (theta, lambda, etc.) — all defaults from the library.
- Output file is always named `harmony_emb_data.h5ad`; the original filename is not preserved in the output path.

## Caveats
- `X_pca` must be precomputed; this recipe does not run PCA.
- `batch_key` must be a column present in `adata.obs`; missing key will raise a KeyError inside `harmonize`.
- Harmony corrects embeddings only — it does not alter the expression matrix; use `X_harmony` for neighbor graph and UMAP, not for DE analysis.
- The `harmony-pytorch` package requires PyTorch; confirm GPU/CPU availability and torch version compatibility.

## In ABA
Implement with `scanpy` + `harmony-pytorch`. `ensure_capability(scanpy, harmony-pytorch)`. After writing `harmony_emb_data.h5ad`, proceed with `sc.pp.neighbors(adata, use_rep="X_harmony")` and `sc.tl.leiden` / `sc.tl.umap` for integrated clustering and visualization.
