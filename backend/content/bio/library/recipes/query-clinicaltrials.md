---
name: query-clinicaltrials
description: Query ClinicalTrials.gov API v2 for clinical study records by condition, intervention, status, or phase
when_to_use: When searching for clinical trials by disease, drug, study status, or phase; or retrieving a specific NCT record
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [clinical trials, ClinicalTrials.gov, NCT, recruiting, cancer, drug trial, phase, intervention, RECRUITING]
produces: [trial records, NCT IDs, study status, intervention details, JSON API response]
domain: database
source: biomni:tool/database.py::query_clinicaltrials
---
# Query ClinicalTrials.gov API v2

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a `prompt` or a direct `endpoint`.
2. If `prompt`: use an LLM with the ClinicalTrials.gov v2 schema to produce a `full_url`.
   - Key parameters: `query.cond` (condition), `query.intr` (intervention), `filter.overallStatus`, `filter.phase`, `filter.studyType`.
   - Phase values: `PHASE1`, `PHASE2`, `PHASE3`, `PHASE4` (comma-separated for multiple).
   - Status values: `RECRUITING`, `COMPLETED`, `NOT_YET_RECRUITING`, etc.
3. Normalize endpoint to full URL, prepending `https://clinicaltrials.gov/api/v2` if relative.
4. If not a specific study lookup (`/studies/{nctId}`), append `pageSize={max_results}`.
5. Issue GET request. On HTTP 400 error, retry with `filter.phase` removed as a fallback.
6. Return JSON result; optionally summarize if `verbose=False`.

## Key decisions
- Base URL: `https://clinicaltrials.gov/api/v2`
- Main search endpoint: `/studies`
- Specific study lookup: `/studies/{nctId}`
- `pageSize` max is 1000; default `max_results=10`.
- On 400 error from invalid phase filter, strip `filter.phase` and retry.

## Caveats
- Phase filter syntax is strict: use comma-separated values, not repeated parameters.
- Some complex LLM-generated filters may trigger 400 errors; the built-in fallback simplifies the query.
- Free-text searches (`query.cond`) may be imprecise for rare disease names — prefer MeSH terms.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
