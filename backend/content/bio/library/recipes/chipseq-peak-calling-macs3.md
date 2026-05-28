---
name: chipseq-peak-calling-macs3
description: Call enriched binding peaks from ChIP-seq data against an input/control BAM using MACS3.
when_to_use: Given a ChIP-seq alignment (BAM/BED) and a matched input/control file, identify genomic regions with significant protein binding or histone-modification enrichment.
requires_tools: [run_python]
capabilities_needed: [macs3]
keywords: [chipseq, peak calling, transcription factor, histone modification, MACS3, MACS2, narrowPeak, broadPeak, enrichment]
produces: [narrowPeak BED file, summits BED file, peaks XLS table]
domain: genomics
source: biomni:tool/genomics.py::perform_chipseq_peak_calling_with_macs2
---
# ChIP-seq Peak Calling with MACS3

Distilled from a biomni implementation (which used MACS2) and **curated to MACS3**
(the maintained successor — `callpeak` is a drop-in for MACS2's, and MACS3 avoids
the legacy MACS2 bioconda build's `undefined symbol: __log_finite` glibc failure).
Implement in ABA with the tools below, not biomni.

## Approach
1. Validate the ChIP-seq file and the control/input file both exist on disk.
2. Create the output directory if needed.
3. Confirm MACS3 is available: `macs3 --version`.
4. Run peak calling (same CLI as MACS2, `macs3` instead of `macs2`):
   ```
   macs3 callpeak \
     -t <chip_seq_file> \
     -c <control_file> \
     -n <output_name> \
     -g <genome_size> \
     -q <q_value> \
     --outdir <output_dir>
   ```
5. Verify the expected outputs:
   - `<prefix>_peaks.narrowPeak` — BED6+4 (summit, -log10 p, -log10 q, fold enrichment).
   - `<prefix>_summits.bed` — single-bp summit per peak.
   - `<prefix>_peaks.xls` — tab-delimited detail table.
6. Count lines in the narrowPeak file to report total peaks.

## Key decisions
- `-g` genome size shorthand: `hs` (human), `mm` (mouse), or an integer effective size.
- `-q` FDR cutoff: default `0.05`; tighten to `0.01` for high-confidence sets.
- Format auto-detected from extension; pass `-f BAMPE` for paired-end, `-f BAM`/`BED` otherwise.
- **Broad marks** (H3K27me3, H3K9me3): add `--broad` → `*_peaks.broadPeak`.
- For **ATAC-seq**, prefer the `atac-seq-differential-accessibility` recipe / MACS3's
  `hmmratac` algorithm rather than plain `callpeak`.

## Caveats
- MACS3 is the maintained tool; install `macs3` (bioconda), not the legacy `macs2`
  (whose bioconda build fails at runtime with `__log_finite` on glibc ≥ 2.31).
- Large BAMs may need a generous timeout.

## In ABA
`run_python` (subprocess shell-out); `ensure_capability("macs3")` (archetype cli,
bioconda). Original impl: `biomni:tool/genomics.py::perform_chipseq_peak_calling_with_macs2`
→ lift to lakeFS later.
