---
name: predict-rna-secondary-structure
description: Predict RNA secondary structure and minimum free energy using ViennaRNA RNAfold
when_to_use: When given an RNA sequence (A/U/G/C) and asked to predict secondary structure, MFE, or stem-loop positions
requires_tools: [run_python]
capabilities_needed: [ViennaRNA]
keywords: [RNA secondary structure, ViennaRNA, RNAfold, MFE, minimum free energy, stem-loop, base pairs, dot-bracket notation]
produces: [dot-bracket structure, MFE kcal/mol, structure TXT file, base-pair list TXT, research log]
domain: microbiology
source: biomni:tool/microbiology.py::predict_rna_secondary_structure
---
# Predict RNA Secondary Structure

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate input: uppercase, strip whitespace, verify all characters are A/U/G/C.
2. Call `RNA.fold(sequence)` from the ViennaRNA Python binding to obtain `(structure, mfe)` in dot-bracket notation.
3. Write structure file: sequence, dot-bracket structure, and MFE in kcal/mol.
4. Parse dot-bracket with a stack to enumerate all base pairs (left_pos, right_pos).
5. Write visualization file: sequence + structure alignment, then list of base pairs with nucleotide identities.
6. Return a log: sequence length, MFE, stem count (number of `(` characters), and output file paths.

## Key decisions
- `RNA.fold` computes the minimum free energy structure (Zuker/Turner nearest-neighbor model).
- Base-pair parsing uses a LIFO stack on `(` push / `)` pop.

## Caveats
- ViennaRNA Python package must be installed (`conda install -c bioconda viennarna` or `pip install ViennaRNA`).
- For longer sequences (> ~2000 nt) consider partition function (`RNA.pf_fold`) for ensemble probabilities.
- Does not predict pseudoknots; use PknotsRG or IPknot for pseudoknotted structures.

## In ABA
Implement with `run_python`; `ensure_capability("ViennaRNA")`. Original impl: `source` -> lift to lakeFS later.
