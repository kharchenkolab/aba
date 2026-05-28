---
name: check-fda-drug-recalls
description: Check FDA drug recalls and enforcement actions for a drug via the OpenFDA API
when_to_use: To look up current or historical FDA recall actions and enforcement data for a specific drug
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [FDA, drug recall, enforcement, Class I recall, Class II recall, drug safety, OpenFDA]
produces: [total recall count, top-5 recall details including classification/reason/date/status/distribution pattern]
domain: pharmacology
source: biomni:tool/pharmacology.py::check_fda_drug_recalls
---
# Check FDA Drug Recalls

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Standardize drug name (strip salt suffixes).
2. Query `https://api.fda.gov/drug/enforcement.json` with `search=openfda.brand_name:<name>&limit=100` via the shared `OpenFDAClient` (rate limiting, retry, 404→empty).
3. Apply optional post-query `classification` filter: keep only records whose `classification` field matches one of the provided class strings (e.g., "Class I", "Class II", "Class III").
4. Display top 5 recall records: recall_number, product_description, classification, reason_for_recall, recall_initiation_date, status, distribution_pattern.
5. Note count of additional recalls beyond the top 5.
6. Inject classification and date-range filter info into the formatted output header.

## Key decisions
- Classification and date-range filters are applied client-side; date filtering modifies the header text only (no server-side date query).
- 100-record limit is used; drugs with many recalls may be truncated.

## Caveats
- OpenFDA enforcement data may lag behind FDA's official recall database; always cross-check with fda.gov/safety/recalls for critical safety decisions.
- Brand-name search may miss recalls filed under a different name or NDC.

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
