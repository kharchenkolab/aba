---
name: fetch-geo-processed-matrices
description: Download processed/supplementary files (count matrices) for a GEO series or sample — 10x mtx triplets, h5/h5ad, or per-sample count tables — and load them for analysis.
when_to_use: You have a KNOWN GEO accession (GSE… or GSM…) and want either (a) its already-processed expression data — count matrices, not raw FASTQ — or (b) just to LIST the study's samples (GSM accessions) + per-sample metadata/characteristics. For a known accession use THIS recipe (it has the GEOparse sample table), not query-geo (which only searches for studies and can't list a series' real per-sample roster). Fast path for scRNA-seq / bulk RNA-seq when authors deposited matrices; try it BEFORE the FASTQ/realignment path.
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

**Check the GSM (sample) level FIRST.** A single sample almost always has its own
supplementary files — for scRNA-seq that's the 10x triplet (`*barcodes.tsv.gz`,
`*features.tsv.gz`/`*genes.tsv.gz`, `*matrix.mtx.gz`), typically only tens of MB.
**Download those directly. Do NOT jump to the series `GSE…_RAW.tar`** (often many
GB — it bundles every sample) unless the GSM genuinely lists no files.

Reliable, GEOparse-free listing — parses the SOFT text directly, so it works for a
GSM *or* a GSE even when GEOparse raises (it can, on some records):

```python
import urllib.request, re
def geo_supp_files(acc):
    url = (f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
           f"?acc={acc}&targ=self&form=text&view=quick")
    txt = urllib.request.urlopen(url, timeout=60).read().decode("utf-8", "replace")
    # GSM lines are !Sample_supplementary_file_N ; GSE lines are !Series_supplementary_file_N
    urls = re.findall(r"^![A-Za-z]+_supplementary_file_\d+\s*=\s*(\S+)", txt, re.M)
    # NCBI serves the same paths over https; ftp:// links are slow/flaky — rewrite them.
    return [u.replace("ftp://ftp.ncbi.nlm.nih.gov", "https://ftp.ncbi.nlm.nih.gov") for u in urls]

acc = "GSM5746259"                       # GSM… (one sample) or GSE… (series)
files = geo_supp_files(acc)
print(f"{acc}: {len(files)} supplementary file(s)"); [print(" ", u) for u in files]
```

(GEOparse convenience alternative: `g = GEOparse.get_GEO("GSM…", destdir="./geo_meta",
silent=True); g.metadata.get("supplementary_file")`. Handy, but it raises on some
records — the urllib method above is the dependable primary.)

If the GSM lists files (the common case) → download them directly (Step 2). Only if
it lists **none** → resolve the parent series (`!Sample_series_id`) and grab the
series supplementary, falling back to `GSE…_RAW.tar` **only as a last resort** —
and then stream it and extract just this sample's members, not the whole archive.

## Step 2 — download the listed files to disk (durable, resumable)

```python
import os, subprocess
os.makedirs("./geo_data", exist_ok=True)
for u in files:                          # the per-sample URLs from Step 1
    dst = os.path.join("./geo_data", os.path.basename(u))
    # curl -L -C - is resumable; -# prints a progress meter. Print our own line
    # too so progress streams to the Console for big files.
    subprocess.run(f"curl -L -C - -o '{dst}' '{u}'", shell=True, check=True)
    print(f"saved {dst} ({os.path.getsize(dst)//1024} KB)", flush=True)
```

The 10x triplet is ~tens of MB — quick. **For anything large, run it as a background
job, write to a durable path, use resumable transfers (`curl -L -C -`), and print a
progress line periodically (every N MB / N seconds) so it streams live** — never
buffer multi-GB files in the kernel. Verify each file exists and is non-empty before
loading. The series `_RAW.tar` last-resort path can be GB: stream it
(`requests.get(stream=True)` + `iter_content`) and `tar.extract()` only the members
whose names start with your `GSM…` prefix.

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
- **Just need a cell count?** Download only `barcodes.tsv.gz` (one barcode = one
  cell) and count its lines — don't pull the (often 50–150 MB) `matrix.mtx.gz`.
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
