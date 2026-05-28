---
name: predict-binding-affinity-protein-1d-sequence
description: Predict drug-target binding affinity (Kd in nM) from a SMILES list and an amino-acid sequence using DeepPurpose DTI models
when_to_use: Estimate binding affinity between small molecules and a protein target when only the 1D sequence is available
requires_tools: [run_python]
capabilities_needed: [DeepPurpose]
keywords: [binding affinity, DTI, drug-target interaction, Kd, SMILES, amino acid sequence, DeepPurpose]
produces: [predicted binding affinity in nM per compound]
domain: pharmacology
source: biomni:tool/pharmacology.py::predict_binding_affinity_protein_1d_sequence
---
# Predict Binding Affinity from Protein 1D Sequence

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate `affinity_model_type` against `["CNN-CNN", "MPNN-CNN", "Morgan-CNN", "Morgan-AAC", "Daylight-AAC"]`.
2. Load the pretrained DeepPurpose `DTI` model: `DTI.model_pretrained(model=<type>_BindingDB)` (hyphens replaced by underscores).
3. For each SMILES, call `utils.data_process` with the compound encoding (first half of the `affinity_model_type` hyphen pair) and target encoding (second half), paired with the amino-acid sequence.
4. Predict; convert from pKd (log scale) to nM: `10^(-pred) / 1e-9`.
5. Return a log with predicted affinity per compound.

## Key decisions
- Model is loaded once and reused for all SMILES in the list.
- BindingDB-trained weights are used; covers a broad range of protein families.
- Affinity is reported in nM for biological interpretability.

## Caveats
- Protein input is a raw amino-acid string (no structural information); accuracy drops for highly novel folds.
- Models trained on BindingDB may underperform for non-kinase targets.
- Very long sequences may require truncation depending on the chosen encoding.

## In ABA
Implement with `run_python`; `ensure_capability("DeepPurpose")`. Original impl: `source` -> lift to lakeFS later.
