---
name: scvi-label-transfer-scanvi
description: scANVI semi-supervised cell-type annotation / label transfer — propagate known labels from a reference onto unlabeled (query) cells in a shared scVI/scANVI latent space.
when_to_use: You have a partially-labeled scRNA-seq object (some cells annotated, others "Unknown"), or an annotated reference + an unannotated query you have combined into one AnnData, and you want to transfer cell-type labels. Builds on a trained scVI model. For mapping onto a SAVED reference you cannot retrain on, use scvi-reference-mapping (scArches) instead.
requires_tools: [run_python]
capabilities_needed: [scvi-tools, scanpy, anndata]
keywords: [scANVI, scvi-tools, label transfer, cell type annotation, semi-supervised, predict, reference query, seed labeling, Tabula Muris, X_scANVI, unlabeled_category, from_scvi_model]
produces: [scanvi_predictions.csv, scanvi_latent.npy, prediction_umap.png, scanvi_model/]
domain: genomics
source: "scvi-tools 1.3.3 tutorial — Integration and label transfer with Tabula Muris / Seed labeling with scANVI (docs.scvi-tools.org/en/1.3.3/tutorials/notebooks/scrna/tabula_muris.html)"
---

# scANVI label transfer (scvi-tools 1.3.3)

scANVI extends scVI with a **semi-supervised** classifier head: it learns from the
cells that ARE labeled and predicts labels for the rest, all within an integrated
latent space. Use it to transfer a curated annotation onto new cells, or to refine
labels from a small set of confident "seed" labels.

**Provision:** `ensure_capability("scvi-tools")` (+ `scanpy`, `anndata`).

**Upstream:** train an scVI model first — see **scvi-integration**. scANVI is
initialized FROM that scVI model via `from_scvi_model`, so the reference and query
must already share one integrated AnnData (set `batch_key` to the
reference/query/technology split during scVI setup).

## Choices to surface with present_plan
- **`labels_key`** — the obs column holding cell types, with unknown cells marked by
  a sentinel string (convention: `"Unknown"`). scANVI trains on the named cells and
  predicts the sentinel ones.
- **`unlabeled_category`** — that sentinel string; must match exactly what you wrote
  into `labels_key`.
- **label granularity** — predictions are only as good as the reference ontology;
  coarse labels transfer more reliably than fine subtypes.
- **`max_epochs` / `n_samples_per_label`** — scANVI fine-tunes briefly on top of
  scVI (tutorial: `max_epochs=20, n_samples_per_label=100`). Subsampling per label
  keeps abundant types from dominating the classifier.
- **hardware** — shorter than scVI but still **background-job** territory; GPU helps,
  CPU OK for small data. So the GPU isn't starved by one CPU core, pass the same
  loader/batch/precision flags shown in **scvi-integration** step 4 to `train()`:
  `scvi.settings.dl_num_workers = 4` and
  `train(accelerator="gpu", devices=1, batch_size=1024, load_sparse_tensor=True, precision="16-mixed")`.

## Procedure

```python
import scvi, scanpy as sc, os
DATA = os.environ["DATA_DIR"]

# adata already has counts layer + batch_key, with a trained scVI model (scvi-integration).
adata = sc.read_h5ad(os.path.join(DATA, "qc_filtered.h5ad"))
scvi_model = scvi.model.SCVI.load(os.path.join(DATA, "scvi_model"), adata=adata)

# 1) Build the labels column: copy known labels, mark the rest "Unknown".
LABELS_KEY = "celltype_scanvi"
adata.obs[LABELS_KEY] = "Unknown"
ref_mask = adata.obs["dataset"] == "reference"           # cells that ARE annotated
adata.obs.loc[ref_mask, LABELS_KEY] = adata.obs.loc[ref_mask, "cell_type"].values

# 2) Initialize scANVI FROM the trained scVI model (no separate setup_anndata needed).
scanvi_model = scvi.model.SCANVI.from_scvi_model(
    scvi_model,
    adata=adata,
    labels_key=LABELS_KEY,
    unlabeled_category="Unknown",
)

# 3) Fine-tune the semi-supervised model (background job).
scanvi_model.train(max_epochs=20, n_samples_per_label=100)

# 4) Predict labels for ALL cells; pull the refined latent space.
adata.obs["C_scANVI"] = scanvi_model.predict(adata)
adata.obsm["X_scANVI"] = scanvi_model.get_latent_representation(adata)

# Optional: per-cell prediction confidence (soft probabilities).
proba = scanvi_model.predict(adata, soft=True)   # cells x classes
adata.obs["scanvi_confidence"] = proba.max(axis=1).values

# 5) Visualize on the scANVI latent.
sc.pp.neighbors(adata, use_rep="X_scANVI")
sc.tl.umap(adata, min_dist=0.3)

scanvi_model.save(os.path.join(DATA, "scanvi_model"), overwrite=True)
```

## Outputs
- `scanvi_predictions.csv` — per-cell predicted label (`C_scANVI`) + confidence.
- `scanvi_latent.npy` — `adata.obsm["X_scANVI"]`.
- `prediction_umap.png` — UMAP coloured by predicted vs. reference labels.
- `scanvi_model/` — saved model (usable as a scArches reference).

## Caveats to surface
- **`unlabeled_category` must exactly equal** the sentinel in `labels_key`, or
  scANVI treats it as a real class.
- Predictions are confined to labels present in the reference — novel query cell
  types get mapped to the nearest known label. Inspect low-confidence cells; flag
  potential novel populations rather than trusting the forced label.
- Quality depends on good upstream scVI integration — verify mixing first.

## Related
- Upstream: **scvi-integration** (the scVI model this builds on).
- Alternative for a frozen, pre-saved reference: **scvi-reference-mapping** (scArches).
