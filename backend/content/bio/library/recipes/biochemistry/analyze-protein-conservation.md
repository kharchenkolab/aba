---
name: analyze-protein-conservation
description: Perform multiple sequence alignment and neighbor-joining phylogenetic analysis to identify conserved protein positions
when_to_use: Given a list of protein sequences (FASTA strings or raw sequences), identify conserved residues and evolutionary relationships
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [protein conservation, multiple sequence alignment, MUSCLE, phylogenetics, neighbor-joining, conservation score, MSA]
produces: [aligned FASTA, Newick tree, per-position conservation TSV, conserved position list]
domain: biochemistry
source: biomni:tool/biochemistry.py::analyze_protein_conservation
---
# Analyze Protein Conservation

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Write input sequences to FASTA (auto-generate headers if sequences lack `>` lines).
2. Run MUSCLE via `Bio.Align.Applications.MuscleCommandline`; if MUSCLE is unavailable fall back to padding all sequences to maximum length with `-`.
3. Build a distance matrix with `Bio.Phylo.TreeConstruction.DistanceCalculator("identity")` and construct a neighbor-joining tree with `DistanceTreeConstructor().nj(dm)`; save as Newick.
4. For each alignment column, count the most common residue; conservation score = fraction of sequences matching that residue. Flag positions with score > 0.8 as highly conserved.
5. Write conservation scores and consensus residue to a TSV; report the first 10 conserved positions in the log.

## Key decisions
- MUSCLE is preferred; the fallback (simple padding) does not produce biologically meaningful alignments and should only be used for debugging.
- Identity-based distance matrix is appropriate for closely related sequences; distantly related proteins may need a substitution-matrix-based calculator.
- Conservation threshold of 0.8 (80%) is hard-coded; adjust for divergent families.

## Caveats
- Requires MUSCLE binary on PATH for the primary alignment path.
- Gap characters (`-`) are included in conservation scoring; columns dominated by gaps may score falsely high.
- Phylogenetic tree is purely distance-based NJ; maximum-likelihood or Bayesian methods are more accurate for publication.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. MUSCLE binary must be available or use Bio.Align fallback. Original impl: `source` -> lift to lakeFS later.
