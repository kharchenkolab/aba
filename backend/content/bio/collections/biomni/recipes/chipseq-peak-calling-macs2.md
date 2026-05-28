---
name: chipseq-peak-calling-macs2
description: Call enriched binding peaks from ChIP-seq data against an input/control BAM using MACS2.
when_to_use: Given a ChIP-seq alignment (BAM/BED) and a matched input/control file, identify genomic regions with significant protein binding or histone modification enrichment.
requires_tools: [run_python]
capabilities_needed: [macs2]
keywords: [chipseq, peak calling, transcription factor, histone modification, MACS2, narrowPeak, enrichment]
produces: [narrowPeak BED file, summits BED file, peaks XLS table]
domain: genomics
source: biomni:tool/genomics.py::perform_chipseq_peak_calling_with_macs2
---
# ChIP-seq Peak Calling with MACS2

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. Validate that the ChIP-seq file and control/input file both exist on disk.
2. Create the output directory if it does not already exist.
3. Confirm MACS2 is available: `macs2 --version`.
4. Run peak calling:
   ```
   macs2 callpeak \
     -t <chip_seq_file> \
     -c <control_file> \
     -n <output_name> \
     -g <genome_size> \
     -q <q_value> \
     --outdir <output_dir>
   ```
5. Verify the three expected output files exist:
   - `<prefix>_peaks.narrowPeak` — BED6+4 with summit position, -log10(p), -log10(q), fold enrichment.
   - `<prefix>_summits.bed` — single-bp summit of each peak.
   - `<prefix>_peaks.xls` — tab-delimited detail table.
6. Count lines in the narrowPeak file to report total peaks called.

## Key decisions
- `-g` genome size shorthand: `hs` (human, default), `mm` (mouse), or an integer effective size.
- `-q` FDR cutoff: default `0.05`; tighten to `0.01` for high-confidence sets.
- Format is auto-detected from file extension (BAM, BED, ELAND, etc.); pass `-f` explicitly if needed.
- `--outdir` is set to the dirname of `output_name` when a directory component is present.

## Caveats
- Timeout is set to 300 s in the original; large BAMs may need more.
- No `--broad` flag is set — this recipe produces narrow peaks; for broad histone marks (H3K27me3, H3K9me3) add `--broad`.
- Library format (single-end vs. paired-end) must be specified with `-f BAMPE` for paired-end ChIP-seq.
- MACS2 works on Python ≤3.9; prefer MACS3 on newer environments.

## In ABA
Implement with `run_python` (subprocess shell-out); `ensure_capability("macs2")`.
Original impl: `biomni:tool/genomics.py::perform_chipseq_peak_calling_with_macs2` → lift to lakeFS later.
