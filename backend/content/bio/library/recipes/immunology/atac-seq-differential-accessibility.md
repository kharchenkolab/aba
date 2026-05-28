---
name: atac-seq-differential-accessibility
description: Call open-chromatin peaks independently on treatment and control ATAC-seq BAMs, then identify differentially accessible regions with MACS3 bdgdiff.
when_to_use: Given two ATAC-seq BAM files (treatment vs. control), detect chromatin accessibility changes between conditions — e.g., stimulated vs. resting immune cells.
requires_tools: [run_python]
capabilities_needed: [macs3]
keywords: [ATAC-seq, chromatin accessibility, differential accessibility, open chromatin, MACS3, MACS2, bdgdiff, hmmratac, epigenomics, immunology]
produces: [treatment narrowPeak, control narrowPeak, cond1 BED (treatment-enriched), cond2 BED (control-enriched)]
domain: immunology
source: biomni:tool/immunology.py::analyze_atac_seq_differential_accessibility
---
# ATAC-seq Differential Accessibility Analysis

Distilled from a biomni implementation (which used MACS2) and **curated to MACS3**
— `callpeak`/`bdgdiff` are drop-in, and MACS3 avoids the legacy MACS2 bioconda
build's `__log_finite` glibc failure. Implement in ABA with the tools below.

## Approach
1. Create the output directory.
2. **Step 1 — Peak calling on treatment BAM** (note `-B/--bdg` so the bedGraph
   pileups bdgdiff needs are written):
   ```
   macs3 callpeak \
     -t <treatment_bam> -f BAM \
     -g <genome_size> \
     -n <prefix>_treatment --outdir <output_dir> \
     --nomodel --shift -100 --extsize 200 -B \
     -q <q_value>
   ```
   Count lines in `<prefix>_treatment_peaks.narrowPeak`.
3. **Step 2 — Peak calling on control BAM** with identical flags and `-n <prefix>_control`.
4. **Step 3 — Differential accessibility with `macs3 bdgdiff`:**
   ```
   macs3 bdgdiff \
     --t1 <treatment>_treat_pileup.bdg \
     --c1 <treatment>_control_lambda.bdg \
     --t2 <control>_treat_pileup.bdg \
     --c2 <control>_control_lambda.bdg \
     --d1 <depth1> --d2 <depth2> \
     --o-prefix <prefix>_differential
   ```
   Reads `<prefix>_differential_cond1.bed` (treatment-enriched) and `_cond2.bed` (control-enriched).
5. **Step 4 — Summary:** report peak counts per condition and total differentially accessible regions.

## Key decisions
- `--nomodel --shift -100 --extsize 200`: ATAC-specific settings that correct for Tn5 insertion bias and call nucleosome-free regions (~200 bp); do NOT use the default ChIP-seq model.
- `-B/--bdg` on `callpeak` is required so `bdgdiff` has its `_treat_pileup.bdg` / `_control_lambda.bdg` inputs.
- `--d1/--d2`: set to actual library depths (million reads) for quantitatively comparable samples — do not leave at `1`.
- `-g`: `hs`/`mm` shorthands or an integer effective genome size; `-q` default `0.05`.
- **MACS3 also ships `hmmratac`** — an ATAC-specific HMM peak caller (`macs3 hmmratac -i ATAC.bam`, paired-end). Prefer it over `callpeak` when you want ATAC-native open-region/nucleosome modeling for a single library; use the `callpeak`+`bdgdiff` flow above for two-condition differential accessibility.

## Caveats
- `bdgdiff` needs the bedGraph pileups from `callpeak -B`; verify they exist before step 3.
- Hard-coded equal depths ignore library-size differences — supply real depths for correct fold-change.
- Install `macs3` (bioconda), not the legacy `macs2`.

## In ABA
`run_python` (subprocess shell-out); `ensure_capability("macs3")` (archetype cli,
bioconda). Original impl: `biomni:tool/immunology.py::analyze_atac_seq_differential_accessibility`
→ lift to lakeFS later.
