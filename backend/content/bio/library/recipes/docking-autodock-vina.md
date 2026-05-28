---
name: docking-autodock-vina
description: Score a list of SMILES against a receptor with AutoDock Vina via the TDC pyscreener Oracle
when_to_use: Virtual screening or lead optimization requiring Vina docking scores for a set of molecules
requires_tools: [run_python]
capabilities_needed: [PyTDC, pyscreener, autodock-vina]
keywords: [docking, AutoDock Vina, virtual screening, binding score, SMILES, pyscreener]
produces: [dict mapping SMILES to docking scores (kcal/mol)]
domain: pharmacology
source: biomni:tool/pharmacology.py::docking_autodock_vina
---
# Docking with AutoDock Vina

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept a list of SMILES strings, a receptor PDBQT/PDB file, a box center `(x, y, z)`, a box size `(dx, dy, dz)`, and an optional CPU count.
2. Instantiate the TDC `Oracle` with `name="pyscreener"` and the receptor/box parameters.
3. Call `oracle(smiles_list)` to obtain docking scores for all compounds in one batch.
4. Return a dict mapping each SMILES to its score, plus a human-readable log.

## Key decisions
- The TDC `Oracle("pyscreener")` handles SMILES-to-3D conversion internally via pyscreener/AutoDock Vina.
- Box center and size must be provided (not discovered automatically); use `run_autosite` first if site is unknown.
- `ncpu` controls Vina parallelism within each scoring call.

## Caveats
- AutoDock Vina scores are in kcal/mol; more negative = more favorable.
- Box must enclose the intended binding site; misdefined boxes yield uninformative scores.
- pyscreener requires ADFR Suite / Vina binary in PATH.

## In ABA
Implement with `run_python`; `ensure_capability("pyscreener", "autodock-vina", "PyTDC")`. Original impl: `source` -> lift to lakeFS later.
