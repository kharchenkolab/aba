---
name: analyze-barcode-sequencing-data
description: Extract, quantify, and cluster barcodes from FASTQ/FASTA sequencing data to identify cell lineages.
when_to_use: When a user has barcode-based lineage-tracing sequencing data and wants barcode counts and lineage groupings via Hamming-distance hierarchical clustering.
requires_tools: [run_python]
capabilities_needed: [biopython, numpy, scipy]
keywords: [barcode sequencing, lineage tracing, FASTQ, FASTA, Hamming distance, hierarchical clustering, cell tracking]
produces: [barcode counts TSV, lineage assignments TSV, research log]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::analyze_barcode_sequencing_data
---
# Analyze Barcode Sequencing Data

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Detect file format from extension (`.fastq`/`.fq` vs FASTA); parse with `Bio.SeqIO.parse`.
2. Extract barcodes from each read using either a direct regex `barcode_pattern` or a flanking-sequence sandwich pattern `5prime(.*?)3prime`.
3. Count barcode occurrences with `collections.Counter`; filter by `min_count` threshold.
4. Write `barcode_counts.tsv` (barcode, count, frequency relative to total reads).
5. If ≥2 barcodes pass the threshold: compute an all-pairs Hamming distance matrix (pad unequal-length barcodes); run `scipy.cluster.hierarchy.linkage` (average method) on condensed distances; cut at `max_dist=3` with `fcluster`.
6. Write `barcode_lineages.tsv` (barcode, count, lineage cluster ID).
7. Return a markdown research log with counts and lineage summary.

## Key decisions
- Flanking-sequence mode is preferred over a raw regex when flanking contexts are known, as it extracts only the variable insert.
- Hamming distance is computed over padded equal-length strings to handle slight length variation.
- Clustering cutoff of 3 mismatches groups sequencing-error variants into the same lineage.

## Caveats
- For very large files, iterating `SeqIO.parse` is memory-efficient but slow; consider streaming with `pyfastx`.
- The Hamming distance matrix is O(n²); impractical for >10,000 unique barcodes.
- Barcode length heterogeneity inflates Hamming distances; confirm uniform barcode design first.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`, `ensure_capability("scipy")`. Original impl: `source` -> lift to lakeFS later.
