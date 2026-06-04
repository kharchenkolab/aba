---
name: scvi-reference-mapping
description: scArches reference mapping — map a new query dataset onto a pre-trained scVI/scANVI reference model via architectural surgery, without retraining the reference.
when_to_use: You have a saved/published scVI or scANVI reference model (your own from scvi-integration, or a hub model) and a NEW query dataset you want to embed in the SAME latent space — and transfer labels — without re-running the whole reference. The query is fine-tuned on top of frozen reference weights. If you can retrain everything together, scvi-label-transfer-scanvi is simpler.
requires_tools: [run_python]
capabilities_needed: [scvi-tools, scanpy, anndata]
keywords: [scArches, scvi-tools, reference mapping, query mapping, load_query_data, prepare_query_anndata, architectural surgery, transfer learning, weight_decay, atlas projection, label transfer, scvi-hub]
produces: [query_latent.npy, query_predictions.csv, mapped_umap.png, query_model/]
domain: genomics
source: "scvi-tools 1.3.3 tutorial — Reference mapping with SCVI-Tools / scArches (docs.scvi-tools.org/en/1.3.3/tutorials/notebooks/multimodal/scarches_scvi_tools.html)"
---

# scArches reference mapping (scvi-tools 1.3.3)

scArches performs **architectural surgery**: it freezes a trained reference model
and adds a few new weights for the query's batch(es), so query cells land in the
reference's latent space while reference embeddings stay fixed. This is how you
project new samples onto an existing atlas (e.g. the Human Lung Cell Atlas) or onto
your own reference, and — with a scANVI reference — transfer cell-type labels onto
the query.

**Provision:** `ensure_capability("scvi-tools")` (+ `scanpy`, `anndata`).

## The reference model MUST be scArches-ready
A reference trained for mapping needs `encode_covariates=True` (so batch is encoded
and the surgery can add query batches). If you train the reference here, use these
non-default args; if reusing one from **scvi-integration**, confirm it was trained
this way (hub models generally are).

```python
import scvi, scanpy as sc, os
DATA = os.environ["DATA_DIR"]

ref = sc.read_h5ad(os.path.join(DATA, "reference.h5ad"))
ref.layers["counts"] = ref.X.copy()
scvi.model.SCVI.setup_anndata(ref, batch_key="batch", layer="counts")
scvi_ref = scvi.model.SCVI(
    ref, n_layers=2, encode_covariates=True,
    use_layer_norm="both", use_batch_norm="none", dropout_rate=0.2,
)
scvi_ref.train()                                    # background job
REF_PATH = os.path.join(DATA, "scvi_ref")
scvi_ref.save(REF_PATH, overwrite=True)
```

## Choices to surface with present_plan
- **query gene/feature alignment** — `prepare_query_anndata` reorders the query to
  the reference's genes and pads missing ones with zeros. The query MUST carry the
  same `batch_key` obs column (its own batch labels are fine — they're new to the
  model). Counts must be in the same layer name as the reference.
- **`weight_decay=0.0`** in `plan_kwargs` — KEEPS reference latent positions fixed
  during query training (the whole point of mapping). Leave it at 0.
- **`max_epochs`** — the query fine-tunes briefly (tutorial uses up to ~200, but it's
  small surgery, much cheaper than reference training).
- **scANVI reference → free label transfer** — if the reference is scANVI, the query
  model's `predict()` gives query cell-type labels.
- **hardware** — cheaper than training a reference; still a **background job** for
  larger queries; GPU helps, CPU OK for small. For the reference-training step, use
  the **scvi-integration** step-4 flags (`scvi.settings.dl_num_workers = 4`; `train(...,
  batch_size=1024, load_sparse_tensor=True, precision="16-mixed")`).

## Mapping the query

```python
query = sc.read_h5ad(os.path.join(DATA, "query.h5ad"))
query.layers["counts"] = query.X.copy()

# 1) Align query genes/vars to the reference's expectations (in place).
scvi.model.SCVI.prepare_query_anndata(query, REF_PATH)

# 2) Architectural surgery: load the reference onto the query.
scvi_query = scvi.model.SCVI.load_query_data(query, REF_PATH)

# 3) Train ONLY the new query weights; weight_decay=0 freezes the reference.
scvi_query.train(max_epochs=200, plan_kwargs={"weight_decay": 0.0})

# 4) Query embedding in the reference latent space.
query.obsm["X_scVI"] = scvi_query.get_latent_representation()
sc.pp.neighbors(query, use_rep="X_scVI"); sc.tl.umap(query)
scvi_query.save(os.path.join(DATA, "query_model"), overwrite=True)
```

## scANVI reference → transfer labels onto the query

```python
# Reference side (once): build scANVI from the scVI reference, then save.
scanvi_ref = scvi.model.SCANVI.from_scvi_model(
    scvi_ref, unlabeled_category="Unknown", labels_key="cell_type",
)
scanvi_ref.train(max_epochs=20, n_samples_per_label=100)
scanvi_ref.save(os.path.join(DATA, "scanvi_ref"), overwrite=True)

# Query side: surgery with the scANVI reference, then predict labels.
scvi.model.SCANVI.prepare_query_anndata(query, os.path.join(DATA, "scanvi_ref"))
scanvi_query = scvi.model.SCANVI.load_query_data(query, os.path.join(DATA, "scanvi_ref"))
scanvi_query.train(max_epochs=100, plan_kwargs={"weight_decay": 0.0})
query.obs["predicted_celltype"] = scanvi_query.predict()
query.obsm["X_scANVI"] = scanvi_query.get_latent_representation()
```

## Outputs
- `query_latent.npy` — query cells in the reference latent space.
- `query_predictions.csv` — transferred labels (scANVI path) + confidence.
- `mapped_umap.png` — reference + query co-embedded; check the query overlaps
  sensible reference regions.
- `query_model/` — saved query model.

## Caveats to surface
- **`weight_decay=0.0` is required** — nonzero lets the query drag reference cells
  around, defeating the mapping.
- Reference must have been trained with `encode_covariates=True`; otherwise the
  surgery has nowhere to attach query-batch weights.
- Query genes not in the reference are dropped; missing ones are zero-padded — large
  panel mismatch degrades mapping. Report the overlap.
- Predicted labels are confined to the reference ontology — query-specific cell types
  get forced to the nearest reference label; flag low-confidence cells as possible
  novel populations.

## Related
- Build/own a reference: **scvi-integration**.
- Joint retraining alternative: **scvi-label-transfer-scanvi**.
- CITE-seq reference mapping: **scvi-totalvi-citeseq** (totalVI supports the same
  `load_query_data` surgery).
