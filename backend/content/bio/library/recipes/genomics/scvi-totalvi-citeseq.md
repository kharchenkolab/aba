---
name: scvi-totalvi-citeseq
description: totalVI joint RNA + surface-protein (CITE-seq) modeling — integrated latent space, denoised RNA & protein, protein background removal, and joint differential expression.
when_to_use: CITE-seq / REAP-seq data with paired transcriptome (RNA counts) and antibody-derived tags (ADT / protein counts). Integrate across batches, denoise both modalities, separate true protein signal from background, and run RNA+protein DE. For RNA-only data use scvi-integration; for RNA+ATAC use scvi-multivi-atac.
requires_tools: [run_python]
capabilities_needed: [scvi-tools, scanpy, anndata]
keywords: [totalVI, TOTALVI, CITE-seq, ADT, antibody derived tags, surface protein, protein expression, multimodal, scvi-tools, protein foreground, denoised expression, joint embedding, batch integration]
produces: [totalvi_latent.npy, denoised_rna.csv, denoised_protein.csv, protein_foreground.csv, totalvi_de.csv, totalvi_model/]
domain: genomics
source: "scvi-tools 1.3.3 tutorial — CITE-seq analysis with totalVI (docs.scvi-tools.org/en/1.3.3/tutorials/notebooks/multimodal/totalVI.html)"
---

# CITE-seq with totalVI (scvi-tools 1.3.3)

totalVI is scVI's multimodal sibling: it jointly models RNA counts and protein (ADT)
counts in one VAE, gives a shared latent space, denoises both modalities, and
explicitly separates real protein signal from ambient/background binding
(foreground probability). It integrates across batches like scVI and supports joint
RNA+protein differential expression.

**Provision:** `ensure_capability("scvi-tools")` (+ `scanpy`, `anndata`).
**Upstream:** QC RNA with **scrna-qc-clustering**; keep **raw RNA counts** and **raw
protein counts**.

## Data layout (the part people get wrong)
- RNA: raw counts in `adata.X` or a `counts` layer; subset to ~4000 HVGs **first**.
- Protein: raw ADT counts in `adata.obsm["protein_expression"]` — a cells × proteins
  matrix (DataFrame keeps protein names). There are only tens of proteins; do NOT
  HVG-filter them.

## Choices to surface with present_plan
- **`batch_key`** — same role as in scVI. totalVI handles **proteins measured in only
  some batches** (a panel differs between runs) — it imputes the missing ones — but
  the protein matrix must contain those columns (NaN/0 for the absent batch).
- **HVG count** — ~4000 RNA HVGs (CITE-seq panels are RNA-deep); proteins all kept.
- **`empirical_protein_background_prior`** — totalVI estimates a per-protein
  background; usually leave the default.
- **epochs / hardware** — heavier than scVI (two modalities). **Background job**;
  GPU strongly preferred for real datasets, CPU OK only for tiny test data. Use
  `train(early_stopping=True)` plus the GPU-utilization flags from **scvi-integration**
  step 4 (`scvi.settings.dl_num_workers = 4`; `train(..., batch_size=1024,
  load_sparse_tensor=True, precision="16-mixed")`) so one CPU core doesn't bottleneck it.

## Procedure

```python
import scvi, scanpy as sc, os
DATA = os.environ["DATA_DIR"]

adata = sc.read_h5ad(os.path.join(DATA, "citeseq.h5ad"))   # RNA in X, ADT in obsm
adata.layers["counts"] = adata.X.copy()
sc.pp.highly_variable_genes(adata, n_top_genes=4000, flavor="seurat_v3",
                            layer="counts", batch_key="batch", subset=True)

# Register both modalities: RNA from a layer, protein from obsm.
scvi.model.TOTALVI.setup_anndata(
    adata,
    layer="counts",
    batch_key="batch",
    protein_expression_obsm_key="protein_expression",
)

model = scvi.model.TOTALVI(adata)
model.train(early_stopping=True)                 # background job; GPU preferred

# Shared latent -> neighbors/UMAP/Leiden like scVI.
adata.obsm["X_totalVI"] = model.get_latent_representation()
sc.pp.neighbors(adata, use_rep="X_totalVI"); sc.tl.umap(adata)
sc.tl.leiden(adata, key_added="leiden_totalVI")

# Denoised expression for BOTH modalities (posterior mean over samples).
rna_denoised, protein_denoised = model.get_normalized_expression(
    n_samples=25, return_mean=True,
)
# Probability each protein is true foreground (vs ambient background) per cell.
protein_fg = model.get_protein_foreground_probability(n_samples=25, return_mean=True)

# Joint RNA + protein differential expression (one-vs-all over clusters).
de = model.differential_expression(groupby="leiden_totalVI", delta=0.5,
                                   batch_correction=True)

model.save(os.path.join(DATA, "totalvi_model"), overwrite=True)
```

## Outputs
- `totalvi_latent.npy` — `adata.obsm["X_totalVI"]`.
- `denoised_rna.csv`, `denoised_protein.csv` — `get_normalized_expression` outputs.
- `protein_foreground.csv` — foreground probability per protein per cell (use to
  call "positive" cells robustly instead of thresholding raw ADT).
- `totalvi_de.csv` — joint RNA+protein DE (`proba_de`, `lfc_mean`, `is_de_fdr_*`).
- `totalvi_model/` — saved model (supports scArches query mapping, see below).

## Notes on the 1.3.3 API
- The classic single-AnnData idiom is `TOTALVI.setup_anndata(..., 
  protein_expression_obsm_key=...)`. 1.3.3 also offers a MuData path —
  `TOTALVI.setup_mudata(mdata, rna_layer=..., protein_layer=..., modalities={...})` —
  if your data is already a MuData object with separate `rna`/`prot` modalities.
- DE columns and interpretation are identical to **scvi-de** (`proba_de`,
  `lfc_mean`, `is_de_fdr_0.05`); proteins and genes appear together in the table.

## Caveats to surface
- Raw counts only, both modalities. Don't CLR/log the protein matrix before totalVI.
- Prefer `get_protein_foreground_probability` over hard ADT thresholds to gate
  positive populations — it accounts for ambient background.
- Heavier to train than scVI; budget GPU time / run as a job.

## Related
- RNA-only: **scvi-integration**. DE detail: **scvi-de**. Reference mapping:
  **scvi-reference-mapping** (totalVI supports `load_query_data` the same way).
