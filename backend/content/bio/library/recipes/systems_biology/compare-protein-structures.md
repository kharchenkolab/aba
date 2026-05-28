---
name: compare-protein-structures
description: Align two protein PDB structures by CA atoms and report RMSD, per-residue displacements, and continuous regions of conformational change
when_to_use: Given two PDB files and chain IDs, structurally compare the proteins and identify regions of significant conformational difference
requires_tools: [run_python]
capabilities_needed: [biopython, numpy]
keywords: [protein structure, RMSD, structural alignment, conformational change, PDB, superimposition, CA atoms, Superimposer]
produces: [overall RMSD, per-residue distance CSV, aligned PDB files, list of conformationally changed regions]
domain: systems_biology
source: biomni:tool/systems_biology.py::compare_protein_structures
---
# Compare Protein Structures

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Parse both PDB files with `Bio.PDB.PDBParser`; extract chains by `chain_id1`/`chain_id2`.
2. Build residue mappings by residue sequence number; find the intersection of residues present in both chains with CA atoms.
3. Align with `Bio.PDB.Superimposer`: set CA atom pairs from common residues, apply transformation to all atoms of structure 2.
4. Compute per-residue CA displacement (numpy linalg norm); flag residues with displacement > 2.0 Å as significantly changed.
5. Identify runs of ≥3 consecutive significantly-changed residues as continuous conformational change regions.
6. Save reference and aligned PDB files; save per-residue distance CSV.

## Key decisions
- Alignment is CA-only (backbone trace); side-chain movements are reflected in the per-residue displacement after superimposition.
- 2.0 Å displacement threshold for "significant" change is configurable in the implementation.
- Continuous-region detection requires ≥3 consecutive residues to reduce noise from single-residue outliers.

## Caveats
- Residue matching is by sequence number only; insertion codes and non-standard residues may cause mismatches.
- PDB files with missing CA atoms for some residues will silently exclude those residues from alignment.
- Global RMSD reflects average alignment quality; local flexible regions are better assessed through per-residue distances.

## In ABA
Implement with `run_python`; `ensure_capability("biopython", "numpy")`. Original impl: `source` -> lift to lakeFS later.
