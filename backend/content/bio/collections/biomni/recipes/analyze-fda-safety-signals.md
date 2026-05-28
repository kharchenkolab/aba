---
name: analyze-fda-safety-signals
description: Comparative safety signal analysis across multiple drugs using FDA FAERS adverse event data
when_to_use: To compare adverse event profiles across two or more drugs and identify prominent safety signals
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [FDA, FAERS, pharmacovigilance, safety signal, adverse event, drug comparison, OpenFDA, MedDRA]
produces: [per-drug report counts and serious-report counts, top common reactions per drug, cross-drug reaction frequency table]
domain: pharmacology
source: biomni:tool/pharmacology.py::analyze_fda_safety_signals
---
# Analyze FDA Safety Signals

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate input: require at least 2 drugs.
2. For each drug, standardize name and query `drug/event` endpoint with `limit=200` via the shared `OpenFDAClient`.
3. Collect all non-empty responses.
4. Extract safety signals across the response list:
   - Per drug: count total and serious reports by matching `patient.drug.medicinalproduct` to each drug name.
   - Find top-3 MedDRA reactions per drug by co-occurrence count.
   - Build a global reaction-pattern table: count and serious/non-serious split for each `reactionmeddrapt`.
   - Build a temporal pattern dict keyed by YYYYMM from `receiptdate`.
5. Format output: per-drug signal summary, top-5 cross-drug reactions, optional trend analysis note if `comparison_period` is supplied.

## Key decisions
- Signal detection is purely descriptive (counts/rates); no disproportionality analysis (ROR, PRR, EBGM) is computed despite `signal_threshold` parameter existing in the signature.
- `comparison_period` populates the output header only; it does not filter the API query.

## Caveats
- Without disproportionality scoring the output cannot be used as true pharmacovigilance signal detection.
- Per-drug matching uses standardized name substring equality; drugs with similar names may be confused.
- 200-record limit per drug may underrepresent high-volume drugs.

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. For rigorous signal detection, supplement with a PRR/ROR calculation layer. Original impl: `source` -> lift to lakeFS later.
