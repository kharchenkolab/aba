---
name: create-harmony-embeddings-scrna
description: Batch-correct an EXISTING scRNA-seq PCA embedding with Harmony (one-line scanpy wrapper) to produce an integrated low-dimensional representation for downstream clustering/UMAP.
when_to_use: AnnData already has PCA computed (.obsm["X_pca"]) and a batch covariate in .obs; you just want the batch-corrected embedding. For the full load→concat→QC→PCA→integrate→cluster flow (multiple raw samples), use harmony-integration-scanpy instead. For an R/Seurat session use harmony-integration.
requires_tools: [run_python]
capabilities_needed: [scanpy, harmonypy]
keywords: [harmony, harmonypy, sc.external.pp.harmony_integrate, batch correction, batch integration, integration, PCA, embeddings, X_pca_harmony, scRNA-seq, single cell, batch effect, scanpy, donor effect]
produces: ["X_pca_harmony embedding added to .obsm", "harmony_emb_data.h5ad saved to data_dir"]
domain: genomics
source: scanpy external API (sc.external.pp.harmony_integrate, backed by harmonypy / Korsunsky 2019)
---
# Create Harmony batch-corrected embeddings for scRNA-seq

Embedding-only step: you already have `obsm["X_pca"]` and just want it
batch-corrected. Use the **one-line scanpy wrapper** — do NOT call `harmonypy`
(or `harmony-pytorch`) directly.

## Approach
1. Load AnnData: `adata = sc.read_h5ad(f"{data_dir}/{adata_filename}")`.
2. **Integrate** — run Harmony on the PCA and store the corrected embedding:
   ```python
   import harmonypy
   ho = harmonypy.run_harmony(adata.obsm['X_pca'], adata.obs, [batch_key])  # batch_key = obs col, e.g. 'sample'
   adata.obsm['X_pca_harmony'] = ho.Z_corr.T   # (n_pcs × n_cells) → (cells × pcs)
   ```
3. Save updated AnnData to `{data_dir}/harmony_emb_data.h5ad`.

> **⚠ Don't use `sc.external.pp.harmony_integrate` under harmonypy ≥2.0** (the
> installed version): it mis-transposes harmonypy 2.0's `Z_corr` and AnnData
> raises `ValueError: Value had shape (n_pcs, n_cells)…`. Call `run_harmony`
> directly as above — the corrected coords are `ho.Z_corr.T` (NOT `Z_corrected`).
> (The wrapper is fine only on harmonypy <2.0.)

## Key decisions
- Uses the scanpy wrapper `sc.external.pp.harmony_integrate` (backed by
  **harmonypy**, pip package `harmonypy`).
- Input embedding must already exist at `.obsm["X_pca"]`; run `sc.pp.pca` first if
  not present.
- The output key defaults to `X_pca_harmony` (override via `adjusted_basis=` only
  if keeping multiple corrections side by side).
- Hyperparameters (theta, lambda, …) default; pass them as extra kwargs and they
  forward to `harmonypy.run_harmony` (e.g. `theta=3` for more aggressive mixing).
- Output file is always named `harmony_emb_data.h5ad`; the original filename is
  not preserved in the output path.

## Caveats
- `X_pca` must be precomputed; this recipe does not run PCA.
- `batch_key` must be a column present in `adata.obs`; a missing key raises a
  KeyError.
- Harmony corrects the embedding only — it does not alter the expression matrix;
  use `X_pca_harmony` for the neighbor graph and UMAP, not for DE analysis.

## In ABA
Implement with `scanpy` + `harmonypy`: `ensure_capability('scanpy')`,
`ensure_capability('harmonypy')`. After writing `harmony_emb_data.h5ad`, proceed
with `sc.pp.neighbors(adata, use_rep="X_pca_harmony")` and `sc.tl.leiden` /
`sc.tl.umap` for integrated clustering and visualization. For the full
multi-sample flow (load→concat→QC→PCA→integrate→cluster, with before/after mixing
plots) see **harmony-integration-scanpy**; for the R/Seurat counterpart see
**harmony-integration**.
