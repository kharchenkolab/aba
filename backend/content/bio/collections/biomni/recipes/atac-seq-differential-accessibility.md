---
name: atac-seq-differential-accessibility
description: Call open-chromatin peaks independently on treatment and control ATAC-seq BAMs, then identify differentially accessible regions with MACS2 bdgdiff.
when_to_use: Given two ATAC-seq BAM files (treatment vs. control), detect chromatin accessibility changes between conditions — e.g., stimulated vs. resting immune cells.
requires_tools: [run_python]
capabilities_needed: [macs2]
keywords: [ATAC-seq, chromatin accessibility, differential accessibility, open chromatin, MACS2, bdgdiff, epigenomics, immunology]
produces: [treatment narrowPeak, control narrowPeak, cond1 BED (treatment-enriched), cond2 BED (control-enriched)]
domain: immunology
source: biomni:tool/immunology.py::analyze_atac_seq_differential_accessibility
---
# ATAC-seq Differential Accessibility Analysis

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. Create the output directory.
2. **Step 1 — Peak calling on treatment BAM:**
   ```
   macs2 callpeak \
     -t <treatment_bam> -f BAM \
     -g <genome_size> \
     -n <prefix>_treatment --outdir <output_dir> \
     --nomodel --shift -100 --extsize 200 \
     -q <q_value>
   ```
   Count lines in `<prefix>_treatment_peaks.narrowPeak`.
3. **Step 2 — Peak calling on control BAM** with identical flags and `-n <prefix>_control`.
4. **Step 3 — Differential accessibility with `macs2 bdgdiff`:**
   ```
   macs2 bdgdiff \
     --t1 <treatment>_treat_pileup.bdg \
     --c1 <treatment>_control_lambda.bdg \
     --t2 <control>_treat_pileup.bdg \
     --c2 <control>_control_lambda.bdg \
     --d1 1 --d2 1 \
     --o-prefix <prefix>_differential
   ```
   Reads `<prefix>_differential_cond1.bed` (treatment-enriched) and `_cond2.bed` (control-enriched).
5. **Step 4 — Summary:** report peak counts per condition and total differentially accessible regions.

## Key decisions
- `--nomodel --shift -100 --extsize 200`: ATAC-seq-specific settings that correct for Tn5 insertion bias and call nucleosome-free regions (~200 bp fragments); do NOT use the default ChIP-seq model.
- `-f BAM` is explicit; required when input is BAM.
- `--d1 1 --d2 1`: depth normalisation factors; set to actual library depths (million reads) for quantitatively comparable samples.
- `genome_size`: `hs` (human) or `mm` (mouse) shorthands; also accepts integer effective genome sizes.
- `q_value` default `0.05`; tighten for cleaner differential sets.

## Caveats
- `bdgdiff` requires the bedGraph pileup files (`_treat_pileup.bdg`, `_control_lambda.bdg`) produced by `callpeak`; these are only generated when `--bdg` flag or MACS2 default behaviour saves them — verify files exist before step 3.
- The original code hard-codes `--d1 1 --d2 1`; this ignores library size differences. Replace with actual sequencing depths for correct fold-change estimation.
- No `--broad` or nucleosome-repeat-length tuning; for bulk ATAC you may want `--nomodel --nolambda` in some workflows.
- MACS2 works on Python ≤3.9; prefer MACS3 on newer environments.

## In ABA
Implement with `run_python` (subprocess shell-out); `ensure_capability("macs2")`.
Original impl: `biomni:tool/immunology.py::analyze_atac_seq_differential_accessibility` → lift to lakeFS later.
