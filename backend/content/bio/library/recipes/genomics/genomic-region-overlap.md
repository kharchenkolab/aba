---
name: genomic-region-overlap
description: Compute pairwise overlaps between two or more sets of genomic regions and summarize overlap size and coverage.
when_to_use: When the user wants to know how much two or more genomic region sets (e.g. ChIP-seq peaks, enhancers, gene bodies, ATAC peaks) share in common — by region count or by base-pair coverage.
requires_tools: [run_python]
capabilities_needed: [pybedtools, bedtools, pandas]
keywords: [genomic overlap, BED intersect, pybedtools, bedtools intersect, enhancer, peak overlap, co-occupancy, genomics]
produces: [pairwise overlap summary TSV, per-pair detailed overlaps BED, research log string]
domain: genomics
source: biomni:tool/genomics.py::analyze_genomic_region_overlap
---
# Genomic Region Overlap Analysis

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. **Accept inputs** as BED file paths or lists of `(chrom, start, end[, name])` tuples. For list inputs, write a temp BED file.
2. **Compute per-set stats**: count regions and total base-pairs for each input set.
3. **Pairwise intersection** — for each pair `(i, j)`:
   - `bedtools[i].intersect(bedtools[j], u=True)` → count of regions from set i that overlap set j (`-u` flag).
   - `bedtools[j].intersect(bedtools[i], u=True)` → reciprocal count.
   - `bedtools[i].intersect(bedtools[j], wo=True)` → overlap base-pairs (last column is overlap width); sum across all records.
   - Compute `pct_of_set1 = overlap_bp / total_bp_set1 * 100` and vice versa.
4. **Save detailed overlaps** for each pair to `{prefix}_{A}_{B}_overlaps.bed` (the `-wo` output).
5. **Save summary** to `{prefix}_summary.tsv` with columns: Set1, Set2, Overlap_Regions, Overlap_BP, Pct_of_Set1, Pct_of_Set2.

## Key decisions
- Uses `pybedtools.BedTool` as the primary interface over raw bedtools subprocess calls.
- `-u` (unique) flag counts each region at most once even if it overlaps multiple features in the other set.
- `-wo` (write overlap) flag provides per-overlap base-pair counts; the overlap width is the last field.
- Overlap percentage is computed against each set's own total base-pairs (not genome size).
- Fallback: if last-field parsing fails, manually compute `max(start_a, start_b)` to `min(end_a, end_b)`.

## Caveats
- Requires bedtools to be installed and on PATH; pybedtools wraps it.
- Large sets can produce very large `-wo` output; memory usage scales with overlap density.
- Percentage metrics only reflect covered bases, not region count fractions — report both to users.
- No statistical significance (e.g. permutation test) is computed; add a shuffled-control comparison for rigorous analyses.

## In ABA
Implement with `run_python`; `ensure_capability(["pybedtools", "pandas"])`. bedtools must be available in the execution environment. Original impl: `biomni:tool/genomics.py::analyze_genomic_region_overlap` → lift to lakeFS later.
