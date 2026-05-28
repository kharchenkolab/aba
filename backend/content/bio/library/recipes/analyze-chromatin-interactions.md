---
name: analyze-chromatin-interactions
description: Identify enhancer-promoter interactions and TADs from Hi-C data using cooler
when_to_use: Analyze 3D genome organization by detecting significant chromatin contacts between regulatory elements and calling topologically associated domains from .cool Hi-C files
requires_tools: [run_python]
capabilities_needed: [cooler, numpy, pandas, scipy]
keywords: [Hi-C, chromatin interactions, TAD, enhancer-promoter, 3D genome, topological domains, cooler]
produces: [enhancer_promoter_interactions.tsv, topological_domains.bed, contact_matrix.npy]
domain: genomics
source: biomni:tool/genomics.py::analyze_chromatin_interactions
---
# Analyze Chromatin Interactions from Hi-C Data

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load Hi-C data: `c = cooler.Cooler(hic_file_path)`; record resolution (`c.binsize`) and chromosomes. Check for ICE balancing weights.
2. Load regulatory elements BED (6-column: chrom, start, end, name, score, strand) with pandas; split into enhancers and promoters by name pattern.
3. Normalize: if balancing weights exist use `c.matrix(balance=True).fetch(chrom)`, otherwise unbalanced.
4. For each chromosome, for each enhancer-promoter pair: compute `interaction_strength = matrix[enh_bin, prom_bin]`; estimate `expected` as nanmean of the corresponding off-diagonal; keep pairs with `fold_enrichment = interaction_strength / expected > 2`.
5. Call TADs via insulation score: for each bin compute sum of contacts in a `window_size=5` flanking square; z-score the trace; local minima below -1 are TAD boundaries; regions between consecutive boundaries are TADs.
6. Export first-chromosome contact matrix as `.npy` for visualization.
7. Save all outputs to `output_dir`.

## Key decisions
- Fold enrichment threshold of 2 is a pragmatic cutoff; adjust for stringency.
- Insulation-score TAD calling is a simple approximation; for publication use HiCExplorer or TADbit.
- Balance falls back gracefully to unbalanced data if weights are absent.

## Caveats
- Only `.cool` (cooler) format is natively supported; `.hic` requires conversion.
- BED name field must contain "enhancer" or "promoter" (case-insensitive) for correct parsing.
- Large chromosomes at high resolution require substantial RAM.

## In ABA
Implement with `run_python`; `ensure_capability("cooler", "numpy", "pandas", "scipy")`. Original impl: `biomni:tool/genomics.py::analyze_chromatin_interactions` — lift to lakeFS later.
