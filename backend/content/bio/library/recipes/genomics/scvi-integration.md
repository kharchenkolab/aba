---
name: scvi-integration
description: scVI deep-generative batch integration for scRNA-seq — learn a batch-corrected latent space, then cluster/UMAP on it.
when_to_use: Multiple scRNA-seq samples/batches (donors, technologies, 10x lanes) you want to integrate into one shared embedding before clustering or annotation. Use when batch effects are visible in a plain PCA/UMAP, or when a downstream task (scANVI label transfer, scArches mapping, scVI DE) needs a trained scVI model. For a single clean sample, plain scanpy (scrna-qc-clustering) is enough.
requires_tools: [run_python]
capabilities_needed: [scvi-tools, scanpy, anndata]
keywords: [scVI, scvi-tools, batch integration, batch correction, harmonization, latent representation, deep generative model, variational autoencoder, single cell, scRNA-seq, X_scVI, leiden, UMAP, donor effect]
produces: [scvi_latent.npy, integrated_umap.png, leiden_clusters.csv, scvi_model/]
domain: genomics
source: "scvi-tools 1.3.3 tutorial — Atlas-level integration of lung data (docs.scvi-tools.org/en/1.3.3/tutorials/notebooks/scrna/harmonization.html)"
---

# scVI batch integration (scvi-tools 1.3.3)

scVI fits a variational autoencoder on **raw counts**, regressing out the batch
covariate, and gives you a low-dimensional latent space (`X_scVI`) that you treat
exactly like a PCA embedding for neighbors/UMAP/Leiden. It is the standard
deep-generative integration method and the foundation for scANVI label transfer,
scArches mapping, and scVI differential expression.

**Provision:** `ensure_capability("scvi-tools")` (pip package `scvi-tools`,
import name `scvi`), plus `scanpy` and `anndata`. First install is heavy.

**Upstream:** run **`scrna-qc-clustering`** first for QC/filtering. scVI consumes
the QC'd object — but feed it **raw counts**, not log-normalized scaled data.

## Choices to surface with present_plan (this is where an advisor earns its keep)
- **`batch_key`** — the single most important argument. It is the obs column whose
  effect scVI removes (e.g. `sample`, `donor`, `10x_lane`, `technology`). Choose
  the nuisance variable; do NOT put a biological variable of interest here or you
  will erase the signal you care about. Multiple nuisances → combine into one
  composite key, or pass extra ones as `categorical_covariate_keys`.
- **`n_latent`** — latent dimensionality (default 10; the integration tutorial uses
  30). 10–20 for modest data, ~30 for atlas-scale/heterogeneous data.
- **`n_layers`** — 1 (default) is fine for small data; 2 for larger/complex.
- **`gene_likelihood`** — `"zinb"` (default) or `"nb"`; `"nb"` is a common, robust
  choice for UMI data.
- **HVGs** — subset to ~2000 highly variable genes computed **with
  `flavor="seurat_v3"` on counts and `batch_key=` set** before setup; keep the
  full counts in a layer if you need all genes later.
- **epochs / hardware** — `train()` auto-picks a sensible epoch count. Training is
  **long-running**: run it as a **background job**. A **GPU** gives a large speedup —
  but use the loader/batch/precision flags in **step 4 below**, or the defaults peg
  one CPU core and leave the GPU mostly idle (~4× slower in practice). **CPU is fine
  for small data** (≲50k cells) but slow for atlas scale.

## Procedure

```python
import scvi, scanpy as sc, anndata as ad, pandas as pd, numpy as np, os
DATA = os.environ["DATA_DIR"]

# 0) Load. If you already have a QC'd object, just read it:
#       adata = sc.read_h5ad(os.path.join(DATA, "qc_filtered.h5ad"))  # from scrna-qc-clustering
#    Loading multiple samples straight from GEO? They are LOOSE, GSM-PREFIXED
#    triplets (GSM..._matrix.mtx.gz / ...barcodes.tsv.gz / ...features.tsv.gz in one
#    dir) — sc.read_10x_mtx will NOT find these, so read the parts EXPLICITLY and
#    concat with a batch key (this becomes scVI's batch_key — see below):
def load_geo_10x(prefix):                       # one GEO loose, GSM-prefixed triplet
    a = sc.read_mtx(f"{DATA}/{prefix}.matrix.mtx.gz").T          # mtx is genes×cells → transpose
    a.obs_names = pd.read_csv(f"{DATA}/{prefix}.barcodes.tsv.gz", header=None)[0].values
    a.var_names = pd.read_csv(f"{DATA}/{prefix}.features.tsv.gz", header=None, sep='\t')[1].values  # col 2 = symbols
    a.var_names_make_unique(); return a
prefixes = ["GSM5746268_...", "GSM5746269_..."]   # the shared per-sample file prefixes
sample_names = ["S1", "S2"]
adata = ad.concat([load_geo_10x(p) for p in prefixes],
                  label="sample", keys=sample_names, index_unique="-")  # batch_key="sample"
# (standard CellRanger dir instead → sc.read_10x_mtx(dir, var_names='gene_symbols'))
# Then run QC/filtering (scrna-qc-clustering) before continuing.

# 1) Preserve raw counts in a dedicated layer (scVI needs integer counts).
adata.layers["counts"] = adata.X.copy()

# 2) HVG selection on counts, batch-aware.
sc.pp.highly_variable_genes(
    adata, n_top_genes=2000, flavor="seurat_v3",
    layer="counts", batch_key="batch", subset=True,
)

# 3) Register the AnnData: point scVI at the counts layer + the batch column.
scvi.model.SCVI.setup_anndata(adata, layer="counts", batch_key="batch")

# 4) Build + train the model (run this step as a background job).
#    The train() defaults underuse the GPU — single-process loader + tiny batch
#    peg one CPU core while the GPU idles. The flags below feed it properly.
#    (CPU thread count is already set sanely by the run_python kernel; nothing to
#    do for threads here.)
import torch
scvi.settings.dl_num_workers = 4                  # multiprocess host-side loading (default 0)
use_gpu = torch.cuda.is_available()               # preflight; False => CPU-only torch install
if use_gpu: torch.set_float32_matmul_precision("high")   # use Tensor Cores for fp32 matmul
model = scvi.model.SCVI(adata, n_layers=2, n_latent=30, gene_likelihood="nb")
model.train(                       # add max_epochs=N to cap
    accelerator="gpu" if use_gpu else "cpu", devices=1,
    batch_size=1024,                              # bigger batches = bigger, fuller GPU kernels (drop to 256–512 on OOM)
    load_sparse_tensor=use_gpu,                   # densify sparse counts ON the GPU, not on one CPU core
    precision="16-mixed" if use_gpu else "32-true",  # mixed precision ≈ free throughput on GPU
)
print("model device:", next(model.module.parameters()).device)   # expect cuda:0

# 5) Latent representation -> use it like a PCA embedding.
SCVI_LATENT_KEY = "X_scVI"
adata.obsm[SCVI_LATENT_KEY] = model.get_latent_representation()

# 6) Neighbors / UMAP / Leiden ON THE scVI LATENT (not on PCA).
sc.pp.neighbors(adata, use_rep=SCVI_LATENT_KEY, n_neighbors=15)
sc.tl.umap(adata, min_dist=0.3)
sc.tl.leiden(adata, resolution=0.5, key_added="leiden_scVI")

# 7) Persist the model for reuse (scANVI / scArches / scVI DE all reload it).
model.save(os.path.join(DATA, "scvi_model"), overwrite=True)
```

## Outputs
- `scvi_latent.npy` — `adata.obsm["X_scVI"]` (cells × n_latent).
- `integrated_umap.png` — UMAP coloured by `batch` (check mixing) and by
  `leiden_scVI` (check biology preserved).
- `leiden_clusters.csv` — per-cell cluster assignment.
- `scvi_model/` — saved model directory; required by the downstream recipes.

## Caveats to surface
- **Raw counts only** in the `counts` layer — never log/scaled data.
- Judge integration by BOTH batch mixing AND biological separation; over-correction
  merges real cell types. Inspect the UMAP coloured both ways.
- Results are stochastic (random init); set `scvi.settings.seed` for reproducibility.
- Don't put your variable of interest in `batch_key`.

## Downstream
- **scvi-label-transfer-scanvi** — annotate cells / transfer labels.
- **scvi-de** — differential expression from this trained model.
- **scvi-reference-mapping** — map new query data onto this model (scArches).
