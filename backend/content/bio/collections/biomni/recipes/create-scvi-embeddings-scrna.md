---
name: create-scvi-embeddings-scrna
description: Generate batch-corrected scVI and scANVI latent embeddings for scRNA-seq data
when_to_use: Produce low-dimensional representations for scRNA-seq data that correct for batch effects (scVI) and leverage cell-type labels (scANVI)
requires_tools: [run_python]
capabilities_needed: [scvi-tools, scanpy, anndata]
keywords: [scVI, scANVI, batch correction, latent space, embedding, scRNA-seq, variational autoencoder]
produces: [scvi_emb_data.h5ad with obsm X_scVI and X_scANVI]
domain: genomics
source: biomni:tool/genomics.py::create_scvi_embeddings_scRNA
---
# Create scVI and scANVI Embeddings for scRNA-seq

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load AnnData: `sc.read_h5ad(f"{data_dir}/{adata_filename}")`.
2. Set up scVI: `scvi.model.SCVI.setup_anndata(adata, batch_key=batch_key)`.
3. Train scVI model: `model = scvi.model.SCVI(adata); model.train()`.
4. Extract latent representation: `adata.obsm["X_scVI"] = model.get_latent_representation()`.
5. Build scANVI from scVI: `lvae = scvi.model.SCANVI.from_scvi_model(model, adata=adata, labels_key=label_key, unlabeled_category="Unknown")`.
6. Train scANVI: `lvae.train()`.
7. Extract scANVI embedding: `adata.obsm["X_scANVI"] = lvae.get_latent_representation(adata)`.
8. Save: `adata.write(f"{data_dir}/scvi_emb_data.h5ad")`.

## Key decisions
- `batch_key`: column in `adata.obs` that identifies technical batches.
- `label_key`: column with partial cell-type labels; cells without labels should be set to `"Unknown"`.
- scANVI is semi-supervised and benefits from even a small fraction of labeled cells.

## Caveats
- Training is GPU-accelerated but works on CPU; large datasets are slow on CPU.
- Raw (unnormalized) count data is expected in `adata.X`.

## In ABA
Implement with `run_python`; `ensure_capability("scvi-tools", "scanpy")`. Original impl: `biomni:tool/genomics.py::create_scvi_embeddings_scRNA` — lift to lakeFS later.
