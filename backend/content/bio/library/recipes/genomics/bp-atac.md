---
name: bp-atac
description: Best-practice single-cell ATAC-seq — snapATAC2 workflow (fragment import, ATAC QC, feature selection, TF-IDF + spectral/LSI embedding, Leiden, gene activity, motif/TF) per the Single-cell Best Practices book.
when_to_use: Use this for scATAC-seq (chromatin accessibility) only — a fragments file or peak/tile x cell matrix taken through the snapATAC2 pipeline (TF-IDF + spectral/LSI, not log-PCA) to QC -> embedding -> clustering -> gene-activity / motif. This is a DIFFERENT modality from scRNA. For the scRNA full rigorous flow see the scrna-best-practices index.
avoid_when: "scRNA gene-expression data (this is chromatin accessibility — a DIFFERENT modality; do not apply the log-PCA scRNA flow); bulk ATAC-seq; when you already have peak calls and only want differential accessibility between conditions (use an ATAC differential-accessibility recipe)."
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, snapatac2]
keywords: [scATAC, ATAC-seq, chromatin accessibility, snapATAC2, TF-IDF, LSI latent semantic indexing, spectral embedding, TSS enrichment, fragments, gene activity, chromVAR motif]
produces: [adata_atac.h5ad, atac_qc.png, atac_umap.png, gene_activity.h5ad]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/chromatin_accessibility/introduction.html"
---

# single-cell ATAC-seq (best practice)

scATAC-seq measures open chromatin per cell. Data is a **peak/tile x cell** matrix, extremely
**sparse and near-binary** (<=2 counts/locus in diploid cells), so the RNA log-PCA pipeline does
NOT apply — the book uses **TF-IDF normalization + spectral/LSI embedding**. Recommended toolkit:
**snapATAC2** (Python; ArchR/Signac in R, episcanpy as alternative).

**Provision:** `ensure_capability(["scanpy","anndata","snapatac2"])`. Input is a **fragments file**
(or a precomputed tile/peak matrix).

## Import fragments + ATAC QC
```python
import snapatac2 as snap
data = snap.pp.import_data("fragments.tsv.gz", chrom_sizes=snap.genome.hg38, sorted_by_barcode=False)
snap.metrics.tsse(data, snap.genome.hg38)        # TSS enrichment per cell
snap.pl.tsse(data)                               # TSSe vs fragment count -> gate real cells
snap.pp.filter_cells(data, min_counts=1000, min_tsse=5)
```
ATAC QC metrics: **fragment count**, **TSS enrichment** (signal-to-noise), **nucleosome signal**.
Counting whole fragments is preferred over reads; avoid binarizing away information.

## Feature selection -> TF-IDF -> spectral/LSI embedding
```python
snap.pp.add_tile_matrix(data)                    # genome bins as features (avoids peak-calling bias)
snap.pp.select_features(data, n_features=250000) # informative tiles
snap.tl.spectral(data)                           # TF-IDF + spectral (LSI-style) low-dim embedding
snap.pp.knn(data)
snap.tl.umap(data)
snap.tl.leiden(data)
snap.pl.umap(data, color="leiden")
```
The book notes peak-calling on ALL cells hides cell-type-specific accessibility — bins, or
**cluster-specific peaks**, are safer feature definitions.

## Downstream — gene activity, motif/TF
```python
gene_mat = snap.pp.make_gene_matrix(data, snap.genome.hg38)  # gene-activity scores -> annotate like RNA
# motif/TF activity: chromVAR-style enrichment over the peak matrix to infer regulators
```
Gene-activity matrices let you annotate clusters with familiar RNA markers and integrate with
scRNA references; **chromVAR** infers per-cell TF motif activity.

## Pitfalls the book calls out
- **Don't use the RNA pipeline** — binary/sparse data needs TF-IDF + spectral/LSI, not log+PCA.
- **Binarization loses information** — count fragments, not reads.
- **Peak definition is the hard part** — calling peaks on all cells hides rare-type accessibility;
  use tiles or per-cluster peaks.
- High sparsity makes per-cell signal weak; QC on TSS enrichment + fragment count is essential.

## In ABA
`snapatac2` carries the ATAC-specific pipeline; once you have a **gene-activity** matrix, clustering
and annotation reuse **`bp-clustering`** / **`bp-annotation`** concepts. Multiome (RNA+ATAC) joins
the multimodal-integration family alongside **`bp-cite-seq`** / **`bp-data-integration`**.
