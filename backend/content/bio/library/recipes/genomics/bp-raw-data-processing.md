---
name: bp-raw-data-processing
description: Best-practice scRNA-seq raw data processing — FASTQ to a filtered count matrix (mapping, barcode correction, UMI dedup, empty-droplet detection) per the Single-cell Best Practices book.
when_to_use: Use this for the raw-data / FASTQ→counts STAGE only — raw 10x/Chromium FASTQs or an unfiltered CellRanger/alevin-fry output that must become (or be understood as) a count matrix BEFORE QC (mapping, barcode correction, UMI dedup, empty-droplet detection). For a ready-made kallisto|bustools path in ABA see quantify-fastq-to-counts-kb; downstream cell QC is bp-quality-control; for the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, kb-python]
keywords: [raw data, FASTQ, alignment, alevin-fry, STARsolo, CellRanger, kallisto, bustools, kb-python, barcode correction, UMI deduplication, empty droplets, knee plot, EmptyDrops, augmented transcriptome, spliced unspliced]
produces: [count_matrix.h5ad, knee_plot.png, mapping_qc.txt]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/introduction/raw_data_processing.html"
---

# scRNA-seq raw data processing (FASTQ -> count matrix)

Turns sequencer FASTQs into a cell x gene count matrix. Four logical steps:
**map reads -> correct cell barcodes -> resolve UMIs (dedup) -> quantify**, then a
first empty-droplet pass. Most of this is a command-line mapper, not Python —
the recipe captures the BOOK'S choices so the agent can pick a mapper and read its output.

## Mapper choice (the first real decision)
- **alevin-fry** (salmon) / **kallisto|bustools (kb-python)** — fast, memory-frugal
  pseudoalignment to a transcriptome. ABA has a ready path: see `quantify-fastq-to-counts-kb`.
- **STARsolo** / **Cell Ranger** — spliced alignment to the FULL genome; slower but
  captures intronic/unannotated reads. Cell Ranger is the 10x commercial standard.
- **zUMIs** — genome-based with integrated QC.

## Reference choice (couples to the mapper)
- **Full genome** — needed if you want introns generically (snRNA-seq); STARsolo/CellRanger.
- **Spliced transcriptome only** — fastest; misses intronic reads, risks spurious multimaps.
- **Augmented transcriptome (spliced + intronic)** — REQUIRED for **RNA velocity** and
  recommended for **single-nucleus** data. alevin-fry / kb-python build these.
  -> if velocity is downstream (`bp-rna-velocity`), choose augmented now to emit spliced+unspliced.

## Barcode correction + UMI dedup (handled by the mapper)
- Valid barcodes via the 10x **whitelist**, **knee/elbow** on the UMI-rank curve, or an
  expected-cell count. Erroneous barcodes corrected within Hamming distance <=1.
- UMIs deduplicated within edit distance 1; multimapping reads ideally resolved by **EM**
  (alevin-fry, STARsolo, kallisto|bustools all support this) rather than discarded.

## Empty-droplet detection (first cell-calling pass)
- **Knee/elbow plot** of barcodes ranked by UMI count — inflection separates cells from
  ambient background; built into all mappers.
- **EmptyDrops** (R/Bioconductor), **DropletQC**, **miQC** — statistical refinements
  applied to the unfiltered matrix when the knee is ambiguous.
- Do NOT over-filter on UMI count alone — combine with biological QC (`bp-quality-control`).

## Loading the result in scanpy
Whatever mapper you used, you land on a matrix you read with scanpy:
```python
import scanpy as sc
adata = sc.read_10x_mtx("filtered_matrix_dir/", var_names="gene_symbols")  # CellRanger/STARsolo
# or sc.read_10x_h5("raw_feature_bc_matrix.h5") for the UNFILTERED matrix (knee plot below)
adata.var_names_make_unique()

# Knee plot on the unfiltered matrix to sanity-check cell calling
import numpy as np, matplotlib.pyplot as plt
counts = np.asarray(adata.X.sum(1)).ravel()
rank = np.argsort(counts)[::-1]
plt.loglog(np.arange(1, len(counts)+1), counts[rank]); plt.xlabel("barcode rank"); plt.ylabel("UMIs")
plt.savefig("knee_plot.png", dpi=120)
adata.write("count_matrix.h5ad")
```

## Pitfalls the book calls out
- Transcriptome-only mapping risks spurious multimaps + missed introns -> prefer augmented.
- Aggressive empty-droplet filtering removes valid low-UMI (small) cells.
- Discarding multimapping UMIs biases against gene families -> use EM assignment.
- Run **FastQC**/**MultiQC** on the biological read (R2 for 10x); barcode/UMI reads (R1) won't
  show normal sequence content. Watch for index hopping with dual indexing.

## In ABA
`ensure_capability(["scanpy","anndata","kb-python"])`. For the actual FASTQ->counts run in
Python, use the **`quantify-fastq-to-counts-kb`** recipe (kb-python). Otherwise this matrix
feeds **`bp-quality-control`** next.
