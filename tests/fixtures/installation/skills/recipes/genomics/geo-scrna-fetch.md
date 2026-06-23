---
name: geo-scrna-fetch
description: Fetch a single-cell RNA-seq dataset from GEO — the series matrix has NO expression data; use the supplementary .mtx/.h5 files.
capabilities_needed: [GEOquery, scanpy]
---
# Fetching scRNA-seq data from GEO

GEO **series matrices** for single-cell studies contain only sample metadata, NOT
the expression matrix. The counts live in the **supplementary files** — per-sample
`*_matrix.mtx.gz` + `barcodes.tsv.gz` + `features.tsv.gz`, or a combined `.h5`.

Steps:
1. Get sample IDs + supplementary URLs: `GEOquery::getGEO(acc, GSEMatrix=FALSE)` (R),
   or parse the SOFT file.
2. Download supplementary files: `GEOquery::getGEOSuppFiles(acc)` (R) or the GEO FTP
   supplementary URLs.
3. Load: `scanpy.read_10x_mtx()` / `scanpy.read_10x_h5()` (Python) or
   `Seurat::Read10X()` (R).
4. Concatenate per-sample matrices (`anndata.concat`) and record the sample of origin.

Do NOT parse the series-matrix `.txt` for counts — it has none, and you will waste
many steps discovering this.
