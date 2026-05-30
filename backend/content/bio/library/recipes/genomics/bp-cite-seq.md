---
name: bp-cite-seq
description: Best-practice CITE-seq / surface-protein (ADT) analysis — MuData multimodal handling, ADT-specific QC (by detected proteins), and DSB/CLR normalization, per the Single-cell Best Practices book.
when_to_use: Use this for the CITE-seq / surface-protein (ADT) modality only — paired RNA + antibody-derived tags handled as muon/MuData with ADT-specific QC and DSB/CLR normalization (do NOT reuse RNA thresholds). For the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, muon, mudata]
keywords: [CITE-seq, surface protein, ADT, antibody derived tags, multimodal, muon, MuData, DSB normalization, CLR centered log ratio, isotype control]
produces: [mdata_cite.h5mu, adt_qc.png, adt_normalized.h5mu]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/surface_protein/quality_control.html"
---

# CITE-seq / surface protein (ADT) analysis (best practice)

CITE-seq pairs gene expression with **antibody-derived tags (ADTs)** measuring surface proteins.
The two modalities have different statistics and need separate handling — the book uses **muon /
MuData** and ADT-specific QC + normalization. ADT counts are NOT negative-binomial: they show a
background (non-specific binding) peak plus a specific peak.

**Provision:** `ensure_capability(["scanpy","anndata","muon","mudata"])`.

## Multimodal structure
```python
import muon as mu, scanpy as sc
mdata = mu.read_10x_h5("filtered_feature_bc_matrix.h5")   # -> mdata['rna'], mdata['prot']
rna, prot = mdata["rna"], mdata["prot"]
```

## QC the ADT modality (by detected PROTEINS, not total counts)
```python
sc.pp.calculate_qc_metrics(prot, inplace=True, percent_top=None)
# look for a 'valley' in n_genes_by_counts -> failed captures; also cap extreme counts
sc.pp.filter_cells(prot, max_counts=100_000)
mdata.update()
mu.pp.filter_obs(mdata, mdata["prot"].obs_names)          # SYNC both modalities
```
Use **sample-wise MAD** outlier detection (per donor/batch), because ADT distributions differ
across batches and hard cutoffs fail:
```python
import numpy as np
from scipy.stats import median_abs_deviation
def is_outlier(a, m, n):
    M = a.obs[m]
    return (M < np.median(M)-n*median_abs_deviation(M)) | (np.median(M)+n*median_abs_deviation(M) < M)
# apply per donor on log1p_total_counts / log1p_n_genes_by_counts at 5 MADs
```

## Normalize the ADT modality
ADTs are normalized differently from RNA — two recommended options:
- **CLR (centered log ratio)** — fast, built into muon:
```python
mu.prot.pp.clr(mdata["prot"])
```
- **DSB (denoised and scaled by background)** — uses **empty droplets** + **isotype controls** to
  subtract ambient/background; preferred when those controls are available (needs the raw/unfiltered
  matrix and isotype tags). The book notes DSB as the more thorough denoiser.

Normalize RNA as usual (`bp-normalization`) on `mdata["rna"]`.

## Downstream
The surface-protein section continues into separate chapters: protein **doublet detection**
(incompatible markers co-occurring flag doublets), per-modality **dimensionality reduction**,
**batch correction**, and joint **annotation** (markers are often cleaner on ADTs than RNA). Joint
embeddings come from multimodal integration (totalVI / WNN / MOFA).

## Pitfalls the book calls out
- **Don't reuse RNA QC thresholds** on ADTs — near-binary protein patterns make total-count
  filtering unreliable; filter on **number of detected proteins**.
- **Sample-aware filtering** — ADT distributions vary by donor/batch.
- **Keep modalities in sync** — always `mu.pp.filter_obs` after filtering one modality.
- Use **isotype controls** for background estimation (DSB).

## In ABA
`muon`/`mudata` for the multimodal object. RNA-side steps reuse **`bp-quality-control`** /
**`bp-normalization`** / **`bp-annotation`**; multimodal integration (totalVI) sits alongside
**`bp-data-integration`** (`scvi-tools` family).
