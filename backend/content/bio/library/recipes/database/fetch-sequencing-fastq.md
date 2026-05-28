---
name: fetch-sequencing-fastq
description: Resolve any sequencing accession (GSE/GSM/SRP/SRR/SRX/PRJNA/ERR…) to run-level metadata and download the raw FASTQ files, preferring direct ENA FASTQ over SRA conversion.
when_to_use: User needs the *raw reads* (FASTQ) for an accession — e.g. to re-align, re-quantify, run a custom pipeline, or when GEO has no usable processed matrices. Handles the GSE→GSM→SRP→SRR resolution and the SRR→FASTQ download.
requires_tools: [run_python]
capabilities_needed: [pysradb, ffq, pandas, sra-tools]
keywords: [SRA, ENA, FASTQ, SRR, SRP, SRX, GSE, GSM, PRJNA, ERR, prefetch, fasterq-dump, pysradb, ffq, raw reads, download, run metadata, samplesheet]
produces: [run metadata table, samplesheet.csv, downloaded FASTQ files on disk]
domain: database
source: "ABA original (2026 acquisition research) — pysradb, ffq, sra-tools"
---
# Fetch sequencing FASTQ from an accession

Two stages: **(1) resolve** the accession to run-level metadata + FASTQ URLs,
**(2) download** the FASTQ. Prefer pulling gzipped FASTQ **directly from ENA**
(no decompression/conversion) and use SRA `prefetch + fasterq-dump` only as a
fallback when ENA lacks the files.

If the user only needs processed expression matrices, use
`fetch-geo-processed-matrices` instead — it is faster and avoids realignment.

`run_python` only. `ensure_capability("pysradb")`, `ensure_capability("ffq")`;
`sra-tools` (provides `prefetch`/`fasterq-dump`, bioconda CLI) only for the SRA fallback.

## Stage 1 — resolve accession → runs + FASTQ links

### Primary: pysradb (actively maintained, v2.5.x, Oct 2025)
Converts GEO↔SRA and emits run metadata including ENA FASTQ URLs.

```python
import subprocess, pandas as pd, io
acc = "GSE176078"   # or GSM…, SRP…, SRX…, SRR…, PRJNA…

# GEO series → SRA project, then → run metadata with ENA fastq links:
def sh(cmd): return subprocess.run(cmd, shell=True, capture_output=True, text=True)

if acc.startswith("GSE"):
    srp = sh(f"pysradb gse-to-srp {acc}").stdout.split()[-1]   # e.g. SRP…
else:
    srp = acc
# Detailed metadata incl. ena_fastq_http / ena_fastq_ftp columns:
out = sh(f"pysradb metadata {srp} --detailed --saveto /tmp/{srp}_meta.tsv")
meta = pd.read_csv(f"/tmp/{srp}_meta.tsv", sep="\t")
print(meta[["run_accession","library_layout"]].head())
# FASTQ link columns present in --detailed output: ena_fastq_ftp(_1/_2), ena_fastq_http(_1/_2)
```

Useful conversions: `gse-to-srp`, `gsm-to-srr`, `srp-to-srr`, `srx-to-srr`,
`srp-to-gse`. All emit small tables.

### Cross-check / alternate: ffq (Pachter lab, v0.3.1)
Metadata + download links only (does NOT download). Good for getting host-specific
links (FTP/AWS/GCP) and for DOI→data. Note: `--ncbi` may return empty as NCBI is
deprecating `.SRA` links — prefer `--ftp` (ENA) or `--aws`.

```python
import json, subprocess
links = subprocess.run(f"ffq --ftp {acc} -o /tmp/{acc}_links.json",
                       shell=True, capture_output=True, text=True)
ftp_urls = json.load(open(f"/tmp/{acc}_links.json"))  # walk for 'url' fields
```

Decision: use **pysradb** as primary (richer tabular metadata, maintained, builds a
samplesheet). Use **ffq** to confirm links or when pysradb's ENA columns are empty
(e.g. very recent submissions not yet synced to ENA).

## Stage 2 — download the FASTQ

### Preferred: direct ENA FASTQ (fastest, already gzipped, resumable)
Build the ENA Portal API link per run and stream to disk.

```python
import subprocess, os
os.makedirs("./fastq", exist_ok=True)
# Resolve via ENA filereport (authoritative for fastq_ftp), then curl with resume:
runs = meta["run_accession"].dropna().unique()
for srr in runs:
    rep = subprocess.run(
        f"curl -s 'https://www.ebi.ac.uk/ena/portal/api/filereport"
        f"?accession={srr}&result=read_run&fields=fastq_ftp&format=tsv'",
        shell=True, capture_output=True, text=True).stdout.strip().splitlines()
    if len(rep) < 2: continue
    for url in rep[1].split("\t")[-1].split(";"):
        if not url: continue
        url = "https://" + url if url.startswith("ftp.") else url
        subprocess.run(f"curl -L -C - -o ./fastq/{os.path.basename(url)} '{url}'",
                       shell=True, check=True)   # -C - resumes partial downloads
```

`curl -C -` (or `wget -c`) makes downloads **resumable** — essential for large
runs over flaky connections. Verify each file's size/`md5` (ENA exposes
`fastq_md5` via the same filereport call) before trusting it.

### Fallback: SRA prefetch + fasterq-dump (when ENA has no FASTQ)
Two steps; `prefetch` first (resumable, validates), then convert. `--split-files`
is **critical** for paired-end or you get an unusable interleaved file.

```python
import subprocess
for srr in runs:
    subprocess.run(f"prefetch {srr} -O ./sra", shell=True, check=True)
    subprocess.run(
        f"fasterq-dump ./sra/{srr}/{srr}.sra --split-files --threads 6 "
        f"--outdir ./fastq --temp ./tmp", shell=True, check=True)
    subprocess.run(f"gzip -f ./fastq/{srr}_*.fastq", shell=True)  # fasterq-dump emits uncompressed
```

`fasterq-dump` needs scratch space ~roughly the FASTQ size for `--temp`; point it
at a disk with room. Multithreaded (`--threads`); the older `fastq-dump` is slow,
single-threaded — avoid it.

## Stage 3 — write a samplesheet
```python
meta[["run_accession","experiment_accession","sample_accession",
      "library_layout"]].to_csv("./fastq/samplesheet.csv", index=False)
```

## Heavy-pipeline alternative: nf-core/fetchngs
For large studies / reproducibility, the production option is the Nextflow
pipeline `nf-core/fetchngs` (`--input ids.csv --outdir … --download_method ftp`,
methods: ftp default / aspera / sratools). It resolves ids → ENA, downloads,
md5-checks, and emits a samplesheet ready for `nf-core/rnaseq`. Needs Nextflow +
container engine (not pip), so it is an external-job option, not in-kernel. Note it
when the user wants a turnkey, audited bulk download; otherwise the pysradb+ENA path
above is lighter.

## Key decisions
- **ENA-direct first** (gzipped, resumable, no conversion) → **SRA fallback** only
  when ENA lacks the files. Same data is in both; ENA is the easier fetch.
- pysradb = primary resolver (maintained, tabular); ffq = link cross-check / DOI.
- Always emit a samplesheet so downstream alignment/quant recipes can consume it.
- 10x scRNA: confirm which read is barcodes (R1) vs cDNA (R2) before quantifying.

## Gotchas
- **`--split-files`** for paired-end via fasterq-dump, or downstream tools choke.
- `fasterq-dump` output is uncompressed — gzip after, and plan for transient 2–3×
  disk (sra + temp + fastq).
- Very recent SRA submissions may not be synced to ENA yet → ENA links empty;
  fall back to SRA, or retry later.
- NCBI is deprecating `.SRA` links; ffq `--ncbi` may be empty — use `--ftp`/`--aws`.
- These downloads are large and slow: **run as a background/streamed job, write to a
  durable disk path, use resume (`curl -C -`/`prefetch`), verify md5/size.** Never
  buffer whole FASTQs in the kernel.
- aspera (ascp) can be faster than FTP but needs the Aspera client + key; optional.

## In ABA
`run_python` (subprocess shell-out). `ensure_capability("pysradb")`,
`ensure_capability("ffq")`, `ensure_capability("pandas")`; `sra-tools` (bioconda CLI,
archetype cli) for the fallback. Long downloads → background job + durable path.
