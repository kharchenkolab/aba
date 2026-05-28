---
name: get-fda-drug-label-info
description: Retrieve FDA-approved drug label sections (indications, warnings, dosing, etc.) via the OpenFDA API
when_to_use: To look up official prescribing information for a drug from the FDA structured product label database
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [FDA, drug label, prescribing information, indications, contraindications, warnings, dosage, OpenFDA]
produces: [brand name, generic name, manufacturer, selected label sections truncated to 500 chars each]
domain: pharmacology
source: biomni:tool/pharmacology.py::get_fda_drug_label_info
---
# Get FDA Drug Label Info

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Standardize drug name (strip common salt suffixes).
2. Query `https://api.fda.gov/drug/label.json` with `search=openfda.brand_name:<name>&limit=50` using the shared `OpenFDAClient` session (rate limiting, retry, validation).
3. Use the first result from the response.
4. Extract `openfda` sub-fields: brand_name, generic_name, manufacturer_name.
5. Extract label text sections; default set: indications_and_usage, contraindications, warnings, dosage_and_administration, adverse_reactions, clinical_pharmacology. Caller can pass a custom `sections` list.
6. Truncate each section to 500 characters with "..." suffix.
7. Return formatted string with metadata and section content.

## Key decisions
- Only the first matching label result is returned; for drugs with multiple formulations, this may not match the desired product.
- Section list is configurable; unknown section keys are silently skipped if not present in the label JSON.

## Caveats
- Content is truncated at 500 chars per section for brevity; full label text may be needed for clinical use.
- Not all drugs have structured labels in OpenFDA; OTC and older drugs may have sparse coverage.

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
