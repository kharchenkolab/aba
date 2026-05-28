---
name: find-n-glycosylation-motifs
description: Scan a protein sequence for canonical N-linked glycosylation sequons (N-X-S/T where X is not P) and report all positions.
when_to_use: When given a protein amino-acid sequence and asked to identify potential N-glycosylation sites or NxS/T sequons.
requires_tools: [run_python]
capabilities_needed: []
keywords: [N-glycosylation, sequon, NxST, glycosite, glycoprotein, glycoengineering, protein sequence, motif scan]
produces: [list of 1-based positions and triplet motifs, research log string]
domain: glycoengineering
source: biomni:tool/glycoengineering.py::find_n_glycosylation_motifs
---
# Find N-Glycosylation Motifs

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Uppercase and validate the input sequence string.
2. Slide a window of width 3 across the sequence (with optional overlap controlled by `allow_overlap`).
3. Flag each triplet where position 0 is N, position 1 is not P, and position 2 is S or T.
4. When `allow_overlap=False` (default), advance index by 3 on a match to avoid double-counting overlapping N residues; otherwise advance by 1.
5. Report total count and 1-based positions with the matched triplet; truncate display at 100 entries.

## Key decisions
- Pure Python — no third-party dependencies required.
- 1-based position numbering to match biological convention.
- Overlap flag defaults to False (standard sequon reporting).

## Caveats
- Does not predict actual glycosylation occupancy; a sequon is necessary but not sufficient.
- Does not account for signal peptide, topology, or steric accessibility.
- For confirmed site prediction, cross-reference with NetNGlyc or UniProt glycosylation annotations.

## In ABA
Implement with `run_python` (pure Python, no extra libs needed). Original impl: `biomni:tool/glycoengineering.py::find_n_glycosylation_motifs` -> lift to lakeFS later.
