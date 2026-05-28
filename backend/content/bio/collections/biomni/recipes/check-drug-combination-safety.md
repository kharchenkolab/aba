---
name: check-drug-combination-safety
description: Assess overall safety of a multi-drug combination by scoring pairwise interactions from DDInter
when_to_use: To get a safety score and clinical recommendations for a proposed drug combination (polypharmacy check)
requires_tools: [run_python]
capabilities_needed: [pandas, pickle]
keywords: [drug combination, polypharmacy, drug safety, DDInter, interaction scoring, contraindication]
produces: [safety level label, numeric safety score, counts by severity, per-pair interaction details, clinical recommendations]
domain: pharmacology
source: biomni:tool/pharmacology.py::check_drug_combination_safety
---
# Check Drug Combination Safety

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load DDInter data (drug info, interaction matrix, name mapping) from pickle files; standardize and fuzzy-match all input drug names.
2. Enumerate all unique drug pairs and collect interactions from the bidirectional matrix.
3. Count interactions by severity: Major, Moderate, Minor.
4. Compute a heuristic safety score (0–100): start at 100, subtract 30 per Major, 10–15 per Moderate, 5 per Minor.
5. Assign a safety label: Safe / Low Risk / Low-to-Moderate Risk / Moderate Risk / High Risk.
6. Format per-pair interaction details (severity, category, clinical significance).
7. Append tiered clinical recommendations: CONTRAINDICATED / CAUTION / MONITOR / AWARENESS / SAFE.
8. Optionally append general management strategies (separate dosing times, TDM, patient education).

## Key decisions
- Safety scoring weights are fixed heuristics (Major=30, Moderate=10–15, Minor=5); they do not reflect real pharmacovigilance ROR/PRR values.
- First drug in the list is not treated specially; all pairs are symmetric.

## Caveats
- Score is indicative only; clinical judgment and patient-specific factors (renal/hepatic function, age) are not incorporated.
- Drugs absent from DDInter are flagged but not penalized, which may underestimate risk.

## In ABA
Implement with `run_python`; `ensure_capability("pandas")`. Requires DDInter pickle data at schema_db path. Original impl: `source` -> lift to lakeFS later.
