---
name: detect-and-characterize-structural-variations
description: Detect and characterize structural variants (DEL, DUP, INV, BND, INS) from a BAM file using LUMPY, filter by quality and size, optionally annotate with COSMIC/ClinVar, and emit a TSV summary.
when_to_use: When given an aligned BAM and reference genome and asked to call or annotate structural variants, including cancer SVs.
requires_tools: [run_python]
capabilities_needed: [samtools, bcftools, lumpyexpress]
keywords: [structural variation, SV, deletion, duplication, inversion, translocation, LUMPY, WGS, cancer genomics, COSMIC, ClinVar, VCF]
produces: [raw VCF, filtered VCF, annotated VCF, TSV summary report, research log string]
domain: cancer_biology
source: biomni:tool/cancer_biology.py::detect_and_characterize_structural_variations
---
# Detect and Characterize Structural Variations

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Extract discordant read-pairs from the BAM with `samtools view -b -F 1294`.
2. Extract split reads by piping `samtools view -h` through `extractSplitReads_BwaMem` (part of LUMPY suite) and re-encoding as BAM.
3. Run `lumpyexpress -B <bam> -S <split.bam> -D <discordant.bam> -o structural_variants.vcf`.
4. Filter calls with `bcftools filter -i 'QUAL>=100 && SVLEN>=100'` to remove low-confidence and tiny events.
5. Count filtered SVs per type (DEL, DUP, INV, BND, INS) using `grep -c SVTYPE=<type>`.
6. Annotate SVs: if COSMIC or ClinVar path is provided, pass to `annotate_sv.py`; otherwise copy filtered VCF as annotated (stub — use AnnotSV or VEP in production).
7. Convert annotated VCF to a tab-separated summary with `bcftools query -f '%CHROM\t%POS\t%INFO/SVTYPE\t%INFO/SVLEN\t%QUAL\n'`.
8. Return a research log listing file paths and per-type SV counts.

## Key decisions
- QUAL >= 100 and SVLEN >= 100 bp thresholds; tune to coverage and read length.
- Annotation step is a stub in the original; prefer AnnotSV or Ensembl VEP for production.
- Split-read extraction uses shell=True pipeline; ensure PATH includes LUMPY binaries.

## Caveats
- Requires samtools, lumpyexpress, extractSplitReads_BwaMem, and bcftools on PATH.
- Short-read SV calling is imprecise near repeats; validate candidates with orthogonal evidence.
- COSMIC/ClinVar annotation requires locally downloaded database files.

## In ABA
Implement with `run_python` (subprocess calls to CLI tools); `ensure_capability("samtools", "bcftools", "lumpy")`. Original impl: `biomni:tool/cancer_biology.py::detect_and_characterize_structural_variations` -> lift to lakeFS later.
