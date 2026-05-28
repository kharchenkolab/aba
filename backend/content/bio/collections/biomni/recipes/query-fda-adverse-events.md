---
name: query-fda-adverse-events
description: Query FDA FAERS adverse event reports for a drug via the OpenFDA API
when_to_use: To retrieve and summarize post-market adverse event reports for a specific drug from the FDA database
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [FDA, FAERS, adverse events, drug safety, pharmacovigilance, OpenFDA, serious adverse event]
produces: [total and serious report counts, top adverse reactions, death/hospitalization/life-threatening tallies, formatted summary with FDA disclaimer]
domain: pharmacology
source: biomni:tool/pharmacology.py::query_fda_adverse_events
---
# Query FDA Adverse Events

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Standardize the drug name (strip salt suffixes: sodium, hydrochloride, sulfate, phosphate, acetate, citrate).
2. Build a `requests.Session` targeting `https://api.fda.gov/drug/event.json`; set User-Agent header.
3. Construct query param: `search=patient.drug.medicinalproduct:<name>&limit=<limit>`.
4. Implement retry logic (3 attempts, exponential backoff); handle HTTP 404 as empty result; handle 429 (rate limit) with 5-second wait; rate-limit self to 5 req/s (0.2 s delay).
5. Validate response: check for `error` key; normalize bare dicts without `meta`.
6. Apply optional post-query filters: `severity_filter` (serious/non_serious checks `result.serious`) and `outcome_filter` (life_threatening/hospitalization/death checks specific seriousness fields).
7. Compute summary statistics: total reports, serious%, deaths, life-threatening, hospitalizations, top-10 MedDRA reactions by count.
8. Return formatted string including FDA disclaimer about voluntary reporting and lack of causation.

## Key decisions
- Filtering is done client-side after fetching `limit` records, not via FDA search query; may miss events outside the fetched window.
- Date range is noted in output but not applied as an API filter in the current implementation.

## Caveats
- OpenFDA FAERS data reflect voluntary reports and are subject to reporting bias; cannot establish causation.
- For large drugs, `limit=100` samples only a fraction of all reports.

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. No API key required for basic queries. Original impl: `source` -> lift to lakeFS later.
