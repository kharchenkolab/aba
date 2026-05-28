---
name: analyze-comparative-genomics-and-haplotypes
description: Align multiple genome FASTA files to a reference, identify variants, and infer haplotype groupings
when_to_use: Compare whole-genome sequences across samples to detect SNPs, characterize shared/unique genomic regions, and group samples by haplotype structure
requires_tools: [run_python]
capabilities_needed: [biopython, numpy, pandas]
keywords: [comparative genomics, haplotype, variant calling, BWA, SNP, whole genome, alignment]
produces: [comparative_analysis.txt, haplotype_analysis.txt, per-sample SAM files]
domain: genomics
source: biomni:tool/genomics.py::analyze_comparative_genomics_and_haplotypes
---
# Analyze Comparative Genomics and Haplotypes

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate inputs; create output directory.
2. Index reference genome with `bwa index reference.fasta` if BWA is available.
3. For each sample FASTA: attempt `bwa mem reference.fasta sample.fasta > sample.sam`; on failure (BWA absent) fall back to direct sequence comparison with `Bio.SeqIO.parse` — find SNPs by character-level diff, reporting up to 10 per sequence.
4. Collect variant lists per sample in `variants_by_sample`.
5. Write `comparative_analysis.txt`: per-sample variant counts plus placeholders for shared/unique region analysis.
6. Write `haplotype_analysis.txt`: assign samples to Haplotype-A/B/C groups by index modulo; record distinguishing variant counts and estimated divergence time (illustrative values — replace with real phylogenetic analysis for publication).
7. Return a research log summarizing all steps.

## Key decisions
- BWA is the preferred aligner; pure-Python fallback enables basic analysis without bioinformatics tools.
- Haplotype grouping in the reference impl is simulated — in production use GATK, variant-calling, and phylogenetic clustering.
- Steps 5-6 outputs serve as scaffolding for a more complete pipeline.

## Caveats
- The haplotype grouping logic is a placeholder; real haplotype inference requires VCF-based methods (e.g., SHAPEIT, BEAGLE).
- Large genomes need considerable disk space for SAM files.
- BWA must be on PATH; install via conda or system package manager.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Original impl: `biomni:tool/genomics.py::analyze_comparative_genomics_and_haplotypes` — lift to lakeFS later.
