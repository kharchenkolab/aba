---
name: quantify-fastq-to-counts-kb
description: Build a reference index and quantify FASTQ reads into a cell×gene (or sample×gene) count matrix using kallisto|bustools via kb-python, with the reference fetched by gget.
when_to_use: You already have FASTQ files (e.g. from fetch-sequencing-fastq) and need to turn them into a count matrix — single-cell (10x etc.) or bulk RNA-seq — without a full alignment pipeline. Pairs with the FASTQ-fetch recipe to close the GEO→matrix loop when only raw reads exist.
requires_tools: [run_python]
capabilities_needed: [kb-python, gget, scanpy, anndata]
keywords: [kallisto, bustools, kb-python, kb count, kb ref, gget ref, pseudoalignment, count matrix, scRNA-seq, single cell, bulk RNA-seq, FASTQ, quantification, 10x, h5ad]
produces: [kallisto index, count matrix (mtx/h5ad), loaded AnnData]
domain: genomics
source: "ABA original (2026 acquisition research) — kb-python, gget"
---
# Quantify FASTQ → counts with kallisto | bustools (kb-python)

Lightweight pseudoalignment quantification. Use after `fetch-sequencing-fastq`
when only raw reads exist and you need a count matrix. Fast and reproducible;
the Pachter-lab `kb-python` wraps kallisto+bustools, and `gget ref` fetches the
genome/transcriptome + annotation by species name.

`run_python` only. `ensure_capability("kb-python")` (brings kallisto+bustools),
`ensure_capability("gget")`, `scanpy`/`anndata` to load the result.

## Step 1 — fetch reference (gget) and build index (kb ref)

```python
import subprocess
# gget lists the right Ensembl FASTA+GTF URLs for a species:
subprocess.run("gget ref -w dna,gtf -o ref.json homo_sapiens", shell=True, check=True)
# Build a kallisto index from genome FASTA + GTF (standard workflow):
subprocess.run(
    "kb ref -i index.idx -g t2g.txt -f1 cdna.fa "
    "ref_dna.fa.gz ref_annotation.gtf.gz", shell=True, check=True)
```

Index build is memory/CPU heavy and slow for full genomes — **run as a background
job**, write `index.idx`/`t2g.txt` to a durable path, and **reuse the index** across
samples/runs (don't rebuild per sample). For common species/technologies you may
prefer a prebuilt index to skip this step.

## Step 2 — count (kb count)

Single-cell (e.g. 10x v3 — R1 = barcode+UMI, R2 = cDNA):
```python
subprocess.run(
    "kb count -i index.idx -g t2g.txt -x 10xv3 -o out_sc "
    "--h5ad -t 8 R1.fastq.gz R2.fastq.gz", shell=True, check=True)
```

Bulk RNA-seq (`-x bulk`, paired reads), or single-nucleus / other chemistries via
the matching `-x` technology string (`kb --list` shows supported technologies).

```python
subprocess.run(
    "kb count -i index.idx -g t2g.txt -x bulk -o out_bulk -t 8 "
    "sample_R1.fastq.gz sample_R2.fastq.gz", shell=True, check=True)
```

## Step 3 — load the matrix
```python
import scanpy as sc
adata = sc.read_h5ad("out_sc/counts_unfiltered/adata.h5ad")   # with --h5ad
# or load the mtx triplet under out_*/counts_unfiltered/ via sc.read_mtx
print(adata)
```

## Key decisions
- **Pick `-x` correctly**: chemistry/technology must match how the FASTQ was
  generated (`10xv2`/`10xv3`/`bulk`/…). Wrong `-x` silently gives garbage. Confirm
  the chemistry from the GEO/SRA metadata or read-length pattern first.
- Build the index **once**, reuse it. Prefer a prebuilt index when available.
- `--h5ad` gives a ready AnnData for the scanpy QC/clustering recipe.

## Gotchas
- Read order matters: barcode read (R1) before cDNA read (R2) for 10x.
- Index build for a full mammalian genome needs substantial RAM and time → background
  job, durable output, generous timeout.
- `kb count` output lives under `out/counts_unfiltered/` — point downstream loaders
  there, not at the top dir.
- This is pseudoalignment; for variant calling or splice-aware needs you want a real
  aligner (STAR/hisat2) instead — note that to the user.

## In ABA
`run_python` (subprocess). `ensure_capability("kb-python")`,
`ensure_capability("gget")`, `ensure_capability("scanpy")`. Index build + count are
heavy → background job + durable path; reuse the index. Feed the resulting matrix to
`scrna-qc-clustering` or `bulk_rnaseq_de`.
