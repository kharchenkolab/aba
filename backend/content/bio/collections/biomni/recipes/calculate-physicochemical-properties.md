---
name: calculate-physicochemical-properties
description: Calculate key physicochemical and drug-likeness properties of a molecule from its SMILES string using RDKit
when_to_use: Drug candidate profiling for Lipinski / Veber rule compliance, lead optimization, or property-based filtering
requires_tools: [run_python]
capabilities_needed: [rdkit]
keywords: [physicochemical properties, drug-likeness, Lipinski, logP, TPSA, molecular weight, HBD, HBA, RDKit, SMILES]
produces: [property table CSV; log with MW, cLogP, TPSA, HBD, HBA, rotatable bonds, ring count, pKa estimates]
domain: pharmacology
source: biomni:tool/pharmacology.py::calculate_physicochemical_properties
---
# Calculate Physicochemical Properties

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Parse SMILES with `Chem.MolFromSmiles`; return error if invalid.
2. Compute properties via RDKit descriptors:
   - `Descriptors.MolWt` — molecular weight
   - `Descriptors.MolLogP` — cLogP
   - `Descriptors.TPSA` — topological polar surface area
   - `Lipinski.NumHDonors` / `NumHAcceptors` — H-bond counts
   - `Descriptors.NumRotatableBonds`, `RingCount`, heavy atom count
3. Estimate acidic groups: count O atoms adjacent to a trisubstituted C; estimate basic groups: count N atoms with degree < 4.
4. Calculate molar refractivity as a drug-likeness proxy via `Crippen.MolMR`.
5. Save all properties to a CSV; return a human-readable research log.

## Key decisions
- pKa and logD7.4 are simplified estimates; for accurate values use ChemAxon Marvin or pkasolver.
- Acidic/basic group counts are heuristic atom-environment rules, not a true ionization model.

## Caveats
- Molecular weight from `Descriptors.MolWt` uses average isotopic masses; use `Descriptors.ExactMolWt` for monoisotopic mass if needed.
- Molar refractivity (MR) is used as a drug-likeness score here, not a strict Ro5 score.

## In ABA
Implement with `run_python`; `ensure_capability("rdkit")`. Original impl: `source` -> lift to lakeFS later.
