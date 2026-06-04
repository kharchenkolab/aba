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
1. **Load.** If you have a `.h5ad`, `sc.read_h5ad(...)`. Loading from GEO are
   **loose, GSM-prefixed** 10x triplets (`GSM..._matrix.mtx.gz` / `...barcodes.tsv.gz`
   / `...features.tsv.gz` all in one dir) — `sc.read_10x_mtx` will NOT find these, so
   read the parts EXPLICITLY and concat MULTIPLE samples with a batch key:
   ```python
   import scanpy as sc, pandas as pd, anndata as ad, os
   D = os.environ["DATA_DIR"]
   def load_geo_10x(prefix):                       # one GEO loose, GSM-prefixed triplet
       a = sc.read_mtx(f"{D}/{prefix}.matrix.mtx.gz").T          # mtx is genes×cells → transpose
       a.obs_names = pd.read_csv(f"{D}/{prefix}.barcodes.tsv.gz", header=None)[0].values
       a.var_names = pd.read_csv(f"{D}/{prefix}.features.tsv.gz", header=None, sep='\t')[1].values  # col 2 = symbols
       a.var_names_make_unique(); return a
   adata = ad.concat([load_geo_10x(p) for p in prefixes],
                     label="sample", keys=sample_names, index_unique="-")  # batch_key="sample"
   # (standard CellRanger dir instead → sc.read_10x_mtx(dir, var_names='gene_symbols'))
   ```
2. **Preserve raw counts BEFORE any normalization** — scVI needs integer counts:
   `adata.layers["counts"] = adata.X.copy()` (do this right after load/QC, before
   `normalize_total`/`log1p`).
3. Set up scVI on the counts layer + batch column:
   `scvi.model.SCVI.setup_anndata(adata, layer="counts", batch_key="sample")`.
4. Train scVI model. The `train()` defaults underuse the GPU (single-process loader
   + tiny batch → one CPU core pegged while the GPU idles); these run parameters fix
   it. CPU thread count is already set sanely by the run_python kernel.
   ```python
   import torch
   scvi.settings.dl_num_workers = 4               # multiprocess host-side loading (default 0)
   use_gpu = torch.cuda.is_available()            # preflight; False => CPU-only torch install
   if use_gpu: torch.set_float32_matmul_precision("high")   # use Tensor Cores
   model = scvi.model.SCVI(adata)
   model.train(
       accelerator="gpu" if use_gpu else "cpu", devices=1,
       batch_size=1024,                           # bigger batches = fuller GPU kernels (256–512 on OOM)
       load_sparse_tensor=use_gpu,                # densify sparse counts on GPU, not one CPU core
       precision="16-mixed" if use_gpu else "32-true",   # mixed precision ≈ free throughput
   )
   ```
5. Extract latent representation: `adata.obsm["X_scVI"] = model.get_latent_representation()`,
   then use it like a PCA embedding: `sc.pp.neighbors(adata, use_rep="X_scVI")` →
   `sc.tl.umap(adata)` → `sc.tl.leiden(adata)`.
6. Build scANVI from scVI: `lvae = scvi.model.SCANVI.from_scvi_model(model, adata=adata, labels_key=label_key, unlabeled_category="Unknown")`.
7. Train scANVI: `lvae.train()`.
8. Extract scANVI embedding: `adata.obsm["X_scANVI"] = lvae.get_latent_representation(adata)`.
9. Save: `adata.write(f"{data_dir}/scvi_emb_data.h5ad")`.

## Key decisions
- `batch_key`: column in `adata.obs` that identifies technical batches.
- `label_key`: column with partial cell-type labels; cells without labels should be set to `"Unknown"`.
- scANVI is semi-supervised and benefits from even a small fraction of labeled cells.

## Caveats
- `train()` auto-detects the GPU, but the **defaults underuse it** (single-process
  data loading + tiny batches → one CPU core pegged, GPU idle). Use the step-4 flags.
  CPU also works but is slow for large data.
- Raw (unnormalized) integer counts are required — keep them in `adata.layers["counts"]`
  (set BEFORE normalization) and point setup at it via `layer="counts"`. Do not feed
  log-normalized data to scVI.

## In ABA
Implement with `run_python`; `ensure_capability("scvi-tools", "scanpy")`. Original impl: `biomni:tool/genomics.py::create_scvi_embeddings_scRNA` — lift to lakeFS later.
