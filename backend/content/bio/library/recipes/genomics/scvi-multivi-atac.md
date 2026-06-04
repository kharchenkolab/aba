---
name: scvi-multivi-atac
description: MultiVI joint single-cell multiome (RNA + ATAC) modeling — integrate paired AND unpaired RNA-only / ATAC-only cells into one latent space, with imputation across modalities.
when_to_use: "Single-cell multiome (10x Multiome — paired RNA + ATAC per cell) and/or a mix of RNA-only and ATAC-only cells you want jointly embedded. MultiVI integrates paired and unpaired cells and imputes the missing modality. For ATAC-only data use PeakVI; for RNA+protein use scvi-totalvi-citeseq; for RNA-only use scvi-integration."
requires_tools: [run_python]
capabilities_needed: [scvi-tools, scanpy, anndata]
keywords: [MultiVI, MULTIVI, multiome, scATAC, ATAC-seq, peaks, paired multiomic, unpaired, RNA ATAC integration, scvi-tools, organize_multiome_anndatas, n_genes, n_regions, modality, PeakVI]
produces: [multivi_latent.npy, multivi_umap.png, multivi_leiden.csv, imputed_expression.csv, multivi_model/]
domain: genomics
source: "scvi-tools 1.3.3 tutorial — Joint analysis of paired and unpaired multiomic data with MultiVI (docs.scvi-tools.org/en/1.3.3/tutorials/notebooks/multimodal/MultiVI_tutorial.html)"
---

# Single-cell multiome with MultiVI (scvi-tools 1.3.3)

MultiVI jointly models gene expression (RNA counts) and chromatin accessibility
(peak/ATAC counts) in one VAE. Its signature strength: it integrates **paired**
cells (both modalities), **RNA-only** cells, and **ATAC-only** cells into a single
latent space, and imputes the missing modality for the unpaired ones.

**Provision:** `ensure_capability("scvi-tools")` (+ `scanpy`, `anndata`).
**Upstream:** QC the RNA side (**scrna-qc-clustering**); QC ATAC (peak calling,
cell filtering) upstream. Keep **raw counts** for both modalities.

## Feature ordering is mandatory
MultiVI requires features concatenated as **genes FIRST, then peaks** in a single
AnnData, and you tell it the split via `n_genes` / `n_regions`. The
`scvi.data.organize_multiome_anndatas()` helper concatenates per-modality AnnDatas
into this layout and tags each cell's `modality`:

```python
import scvi, scanpy as sc, os
DATA = os.environ["DATA_DIR"]

paired   = sc.read_h5ad(os.path.join(DATA, "multiome_paired.h5ad"))   # genes+peaks
rna_only = sc.read_h5ad(os.path.join(DATA, "rna_only.h5ad"))          # genes
atac_only= sc.read_h5ad(os.path.join(DATA, "atac_only.h5ad"))         # peaks

# Concatenate into the required genes-then-peaks layout; tags obs["modality"].
adata = scvi.data.organize_multiome_anndatas(paired, rna_only, atac_only)
adata.var["modality"]   # 'Gene Expression' rows precede 'Peaks' rows
```

## Choices to surface with present_plan
- **`n_genes` / `n_regions`** — counts of RNA features and peak features; they MUST
  match the genes-then-peaks ordering or the modalities get swapped.
- **`batch_key`** — MultiVI uses batch to encode which modality each cell carries
  (paired / expression / accessibility) plus any technical batch. `organize_*` sets
  up `modality`; pass it (or a composite) as the batch covariate.
- **peak set** — large peak panels are heavy; consider restricting to a sensible peak
  set. ATAC is sparse — expect slower training than RNA-only.
- **epochs / hardware** — the heaviest of the family (two modalities, many peaks).
  **Background job**; GPU strongly preferred, CPU only for tiny test data. Apply the
  **scvi-integration** step-4 flags (`scvi.settings.dl_num_workers = 4`; `train(...,
  batch_size=1024, load_sparse_tensor=True, precision="16-mixed")`) so the GPU stays
  fed instead of waiting on one CPU core.

## Procedure

```python
# Register: tell MultiVI the modality split via batch_key on obs["modality"].
scvi.model.MULTIVI.setup_anndata(adata, batch_key="modality")

model = scvi.model.MULTIVI(
    adata,
    n_genes=(adata.var["modality"] == "Gene Expression").sum(),
    n_regions=(adata.var["modality"] == "Peaks").sum(),
)
model.train()                                  # background job; GPU preferred

# Shared latent across all cells (paired + unpaired) -> neighbors/UMAP/Leiden.
adata.obsm["X_multiVI"] = model.get_latent_representation()
sc.pp.neighbors(adata, use_rep="X_multiVI"); sc.tl.umap(adata)
sc.tl.leiden(adata, key_added="leiden_multiVI")

# Impute the missing modality for unpaired cells (denoised expression/accessibility).
imputed = model.get_normalized_expression()    # imputed gene expression
acc     = model.get_accessibility_estimates()   # imputed accessibility

model.save(os.path.join(DATA, "multivi_model"), overwrite=True)
```

## Outputs
- `multivi_latent.npy` — `adata.obsm["X_multiVI"]` (all cells, all modalities).
- `multivi_umap.png` — UMAP coloured by `modality` (check paired/unpaired mix) and
  by `leiden_multiVI`.
- `multivi_leiden.csv` — per-cell clusters.
- `imputed_expression.csv` — imputed RNA for ATAC-only cells (and vice versa).
- `multivi_model/` — saved model.

## Notes on the 1.3.3 API
- 1.3.3 also exposes a MuData path (`MULTIVI.setup_mudata(mdata, modalities={
  "rna_layer": ..., "atac_layer": ...})`) if your data is already a MuData with
  separate `rna`/`atac` modalities — then `n_genes`/`n_regions` come from each
  modality's `.var` length. The AnnData + `organize_multiome_anndatas` path above is
  the classic idiom and is robust for mixed paired/unpaired inputs.
- For **ATAC-only** data (no RNA), use **PeakVI** (`scvi.model.PEAKVI`) instead —
  same setup_anndata / train / get_latent_representation pattern, accessibility only.

## Caveats to surface
- **Genes-then-peaks ordering + correct `n_genes`/`n_regions` is critical** — get it
  wrong and the model mislabels modalities silently.
- Raw counts both modalities; ATAC peaks are binary-ish/sparse — don't pre-binarize
  unless you know you need to (see PoissonVI for fragment counts).
- Heaviest training in this family — budget GPU/job time accordingly.

## Related
- RNA-only: **scvi-integration**. ATAC-only: PeakVI. DE: **scvi-de** pattern applies
  to the RNA modality. Reference mapping: **scvi-reference-mapping**.
