---
name: analyze-cas9-mutation-outcomes
description: Categorise Cas9-induced insertion and deletion mutations across sequencing reads by aligning edited sequences to reference and classifying indel size
when_to_use: When given reference sequences and pools of Cas9-edited sequencing reads and asked to quantify editing efficiency and mutation type distribution
requires_tools: [run_python]
capabilities_needed: [biopython, pandas]
keywords: [CRISPR, Cas9, indel, deletion, insertion, mutation outcome, pairwise alignment, editing efficiency, amplicon sequencing]
produces: [detailed_results_csv, summary_csv]
domain: genetics
source: biomni:tool/genetics.py::analyze_cas9_mutation_outcomes
---
# Analyze Cas9 Mutation Outcomes

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `reference_sequences` (dict: id → sequence) and `edited_sequences` (dict: id → dict of read_id → sequence); optional `cell_line_info` dict.
2. For each target site and each read, run `Bio.pairwise2.align.globalms(ref, edited, match=2, mismatch=-1, open=-2, extend=-0.5)` to get the best global alignment.
3. Walk the aligned pair character by character, tracking gap runs: gaps in the reference = insertions; gaps in the edited sequence = deletions; accumulate total insertion/deletion counts per read.
4. Classify each read into one of seven categories: `no_mutation`, `short_deletion` (1-10 bp), `medium_deletion` (11-30 bp), `long_deletion` (>30 bp), `single_insertion` (1 bp), `longer_insertion` (>1 bp), `indel` (both present).
5. Aggregate counts per target site and per cell line; compute percentages.
6. Write detailed per-read CSV (`{prefix}_detailed_results.csv`) and per-cell-line summary CSV (`{prefix}_summary.csv`).

## Key decisions
- Uses global alignment (globalms) to handle full-length amplicon reads; local alignment would miss terminal indels.
- Cell-line stratification is optional; if absent all reads are grouped as "Unknown".
- Categories mirror CRISPResso-style bins; adjust thresholds to match downstream analysis expectations.

## Caveats
- `Bio.pairwise2` is deprecated in recent Biopython; prefer `Bio.Align.PairwiseAligner` for new code.
- Very long deletions or rearrangements may align poorly with global mode; consider local alignment or minimap2 for larger amplicons.
- Does not deconvolve mixed allele populations (heterozygous edits).

## In ABA
Implement with `run_python`; `ensure_capability("biopython", "pandas")`. Original impl: `source` -> lift to lakeFS later.
