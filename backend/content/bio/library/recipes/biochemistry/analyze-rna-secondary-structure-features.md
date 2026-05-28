---
name: analyze-rna-secondary-structure-features
description: Calculate numeric structural features (stems, loops, pairing fraction, free energy) from an RNA dot-bracket structure
when_to_use: Given a dot-bracket string (and optional sequence), extract quantitative descriptors of an RNA secondary structure
requires_tools: [run_python]
capabilities_needed: [numpy]
keywords: [RNA, secondary structure, dot-bracket, stem, loop, base pair, free energy, nearest-neighbor, structural features]
produces: [stem count, loop count, pairing fraction, estimated free energy, per-stem position table]
domain: biochemistry
source: biomni:tool/biochemistry.py::analyze_rna_secondary_structure_features
---
# Analyze RNA Secondary Structure Features

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate that all characters are in `().[]{}`; verify bracket balance using a stack.
2. Parse paired positions into a sorted list of `(i, j)` tuples; detect consecutive pairs as stems.
3. Compute stem lengths, loop sizes (unpaired bases between consecutive stem ends), paired/unpaired base counts.
4. If sequence provided: apply simplified nearest-neighbor energies (AU/UA=-0.9, GC/CG=-2.1, GU/UG=-0.5 kcal/mol) per base pair in each stem; sum for total estimated free energy.
5. Report stem details (positions, stability) in structured text log.

## Key decisions
- Only `()`, `[]`, `{}` bracket types recognized; pseudoknots indicated by mismatched types are rejected.
- Free energy model is a simplified lookup — not full Turner parameters; suitable for relative comparison, not publication-quality thermodynamics.
- Loop size calculated as gap between last pair of one stem and first residue of the next stem.

## Caveats
- Does not call RNAfold, Vienna RNA, or any external folding engine — purely parses a pre-computed structure.
- Energy estimates are illustrative; use ViennaRNA or mfold for accurate thermodynamics.
- Multi-loop and internal-loop detection is not implemented; only hairpin-like loops between consecutive stems.

## In ABA
Implement with `run_python`; pure Python suffices (numpy only for optional array ops). Original impl: `source` -> lift to lakeFS later.
