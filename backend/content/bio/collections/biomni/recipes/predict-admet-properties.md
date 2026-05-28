---
name: predict-admet-properties
description: Predict ADMET properties (absorption, distribution, metabolism, excretion, toxicity) for a list of SMILES using DeepPurpose pretrained models
when_to_use: Early-stage drug candidate profiling for pharmacokinetic and toxicity risk assessment
requires_tools: [run_python]
capabilities_needed: [DeepPurpose]
keywords: [ADMET, pharmacokinetics, solubility, bioavailability, BBB, CYP, toxicity, SMILES, drug-likeness]
produces: [per-compound ADMET property table covering 16 endpoints]
domain: pharmacology
source: biomni:tool/pharmacology.py::predict_admet_properties
---
# Predict ADMET Properties

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate `ADMET_model_type` against `["MPNN", "CNN", "Morgan"]`.
2. Load 16 pretrained DeepPurpose `CompoundPred` models (one per endpoint: AqSolDB, Caco2, HIA, Pgp_inhibitor, Bioavailability, BBB_MolNet, PPBR, CYP2C19, CYP2D6, CYP3A4, CYP1A2, CYP2C9, ClinTox, Lipo_AZ, Half_life_eDrug3D, Clearance_eDrug3D).
3. For each SMILES: encode with `utils.data_process` using the chosen encoding, call `model.predict`, apply ×100 for percentage endpoints.
4. Compile results into a structured research log grouped by ADME category.

## Key decisions
- Model weights are fetched from the DeepPurpose hub on first call; subsequent calls reuse cached weights.
- All 16 models use the same encoding type (MPNN/CNN/Morgan) for consistency.
- Percentage endpoints (HIA, Pgp, Bioavailability, BBB, PPBR, CYP*) are multiplied by 100 after raw prediction.

## Caveats
- DeepPurpose models were trained on public datasets; predictions are approximations, not regulatory-grade.
- MPNN is the most accurate but slowest; Morgan is fastest.
- Model loading for all 16 tasks at startup is slow; consider batching multiple SMILES in one call.

## In ABA
Implement with `run_python`; `ensure_capability("DeepPurpose")`. Original impl: `source` -> lift to lakeFS later.
