---
name: fetch-geo-processed-matrices
description: Download processed/supplementary files (count matrices) for a GEO series or sample — 10x mtx triplets, h5/h5ad, or per-sample count tables — and load them for analysis.
when_to_use: User has a GEO accession (GSE… or GSM…) and wants the *already-processed* expression data (count matrices, not raw FASTQ). This is the fast path for scRNA-seq and most bulk RNA-seq when the authors deposited matrices. Try this BEFORE the FASTQ/realignment path.
requires_tools: [run_python]
capabilities_needed: [GEOparse, pandas, scanpy, anndata]
keywords: [GEO, GSE, GSM, count matrix, supplementary files, 10x, mtx, barcodes, features, h5ad, h5, processed data, expression matrix, scRNA-seq, bulk RNA-seq, download]
produces: [downloaded supplementary files on disk, loaded AnnData / count matrix, sample metadata table]
domain: database
source: "ABA original (2026 acquisition research) — GEOparse, scanpy"
---
# Fetch GEO processed count matrices

Most depositors upload **processed** expression data as GEO *supplementary files*,
either at the **series** level (one bundle for the study) or the **sample** level
(one file per GSM). For scRNA-seq this is almost always there; downloading and
loading it is far faster and more reliable than re-fetching FASTQ and re-aligning.
**Try this recipe first.** Fall back to `fetch-sequencing-fastq` only if the
series has no usable processed matrices, or the user explicitly needs raw reads.

`run_python` only; `ensure_capability("GEOparse")` and (`scanpy`/`anndata` for
loading 10x/h5ad, `pandas` for plain tables).

## Decision point — what kind of processed file is here?

GEO supplementary layout is not standardized. Before loading, **list the files
first** and branch on what you see:

- **10x triplet** (`*matrix.mtx.gz` + `*barcodes.tsv.gz` + `*features.tsv.gz`/`*genes.tsv.gz`):
  scanpy `sc.read_mtx` / `sc.read_10x_mtx`. Files are often prefixed per-sample
  (`GSM..._matrix.mtx.gz`) and must be regrouped into one dir per sample.
- **HDF5** (`*.h5ad`, `*.h5`, `*filtered_feature_bc_matrix.h5`): `sc.read_h5ad`
  or `sc.read_10x_h5`. Single richest case.
- **Flat table** (`*.csv.gz`, `*.txt.gz`, `*.tsv.gz`, `*counts*`): `pandas.read_csv`
  (genes × samples or cells). Watch the separator and the orientation.
- **RDS / Seurat / loom**: harder; note it and prefer the FASTQ path or ask the user.

## Step 1 — list supplementary files (cheap, no big download)

```python
import GEOparse
acc = "GSE176078"                      # GSE… (series) or GSM… (single sample)
gse = GEOparse.get_GEO(geo=acc, destdir="./geo_meta", silent=True)  # fetches SOFT only

# series-level supplementary files
print("SERIES files:", gse.metadata.get("supplementary_file", []))
# per-sample supplementary files
for gsm_name, gsm in gse.gsms.items():
    print(gsm_name, gsm.metadata.get("supplementary_file", []))
```

`get_GEO` downloads only the SOFT metadata, so this is fast. Inspect the URLs to
decide which branch above applies before pulling gigabytes.

## Step 2 — download supplementary files to disk (durable)

```python
# Series-level bundle(s):
gse.download_supplementary_files(directory="./geo_data", download_sra=False)
# download_sra=False is important: never let GEOparse pull SRA here — that is
# the FASTQ path and is huge/slow. Keep this recipe to processed files only.
```

This writes into `./geo_data/<GSM>/...`. For a series-level tarball
(`GSE..._RAW.tar`) you may instead fetch the single series URL directly with
`requests`/`urllib` streaming and `tarfile.extractall`.

**Run big downloads as a background/streamed job and write to a durable path on
disk** — do not buffer multi-GB files in the kernel. Stream with
`requests.get(url, stream=True)` + chunked `iter_content`, or shell out to
`curl -L -C - -o` (resumable). Always verify the file exists and is non-empty
before loading.

## Step 3 — load by branch

### 3a. 10x mtx triplet
```python
import scanpy as sc, glob, os, shutil
# Regroup a per-sample triplet into one dir with canonical names:
def stage_10x(sample_prefix, src_dir, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    for canon, pats in {
        "matrix.mtx.gz":   ["*matrix.mtx.gz"],
        "barcodes.tsv.gz": ["*barcodes.tsv.gz"],
        "features.tsv.gz": ["*features.tsv.gz", "*genes.tsv.gz"],
    }.items():
        hit = next((f for p in pats
                    for f in glob.glob(os.path.join(src_dir, sample_prefix + p))), None)
        if hit: shutil.copy(hit, os.path.join(dst_dir, canon))
    return dst_dir

adata = sc.read_10x_mtx(stage_10x("GSM5354513_", "./geo_data/GSM5354513", "./tenx/GSM5354513"))
```

### 3b. HDF5
```python
import scanpy as sc
adata = sc.read_10x_h5("./geo_data/GSM.../filtered_feature_bc_matrix.h5")  # cellranger h5
# or: adata = sc.read_h5ad("./geo_data/.../something.h5ad")
```

### 3c. flat table
```python
import pandas as pd
df = pd.read_csv("./geo_data/GSM.../GSE..._counts.csv.gz", index_col=0)
# Decide orientation: genes are usually rows. If columns look like ENSG/symbols, transpose.
import anndata as ad
adata = ad.AnnData(df.T)   # AnnData wants cells/samples × genes
```

## Step 4 — attach sample metadata

```python
import pandas as pd
meta = pd.DataFrame({g: s.metadata for g, s in gse.gsms.items()}).T
# characteristics_ch1 holds the useful condition/tissue/genotype annotations.
```

## Key decisions
- **Processed-first**: this recipe is the default for "get the data from GSE…".
  Only go to FASTQ if matrices are absent/unusable or reads are truly needed.
- `download_sra=False` always — keep SRA out of this path.
- Per-sample vs series-level files: scRNA-seq is usually per-GSM 10x triplets;
  bulk is often one series-level count table.

## Gotchas
- **No naming standard.** You must list files and branch; do not assume 10x.
- 10x triplets are commonly prefixed per sample and need regrouping (Step 3a).
- `features.tsv.gz` vs legacy `genes.tsv.gz`; CellRanger v2 vs v3 layouts differ.
- Flat tables: check separator (`\t` vs `,`) and orientation before trusting it.
- GEO mirrors throttle; use resumable downloads (`curl -C -`) for large `_RAW.tar`.
- Some series only deposit RDS/Seurat objects → note it; FASTQ path may be cleaner.
- `GEOparse` caches SOFT files in `destdir`; reuse it to avoid refetching metadata.

## In ABA
`run_python`; `ensure_capability("GEOparse")`, `ensure_capability("scanpy")`
(pulls anndata), `ensure_capability("pandas")`. Big file pulls → background/streamed
job, durable disk path, verify size before load.
