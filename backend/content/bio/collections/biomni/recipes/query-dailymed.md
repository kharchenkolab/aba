---
name: query-dailymed
description: Query the DailyMed RESTful API for FDA drug labeling and structured product labeling (SPL) data
when_to_use: When retrieving FDA drug labels, NDC codes, RxCUI mappings, SPL documents, or drug packaging information
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [DailyMed, FDA, drug label, SPL, NDC, RxCUI, UNII, drug class, drug names, package insert]
produces: [drug label data, SPL documents, NDC codes, RxCUI identifiers, JSON or XML response]
domain: database
source: biomni:tool/database.py::query_dailymed
---
# Query DailyMed API

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a `prompt` or a direct `endpoint`; also accept `format` (json or xml, default json).
2. If `prompt`: use an LLM with the DailyMed API schema to produce a `full_url` (including format extension).
   - Resources: `applicationnumbers`, `drugclasses`, `drugnames`, `ndcs`, `rxcuis`, `spls`, `uniis`.
   - SPL sub-resources: `/spls/{setid}/history`, `/spls/{setid}/media`, `/spls/{setid}/ndcs`, `/spls/{setid}/packaging`.
3. Normalize endpoint: prepend `https://dailymed.nlm.nih.gov/dailymed/services/v2` if relative.
4. If using direct endpoint and no format extension present, append `.{format}`.
5. Issue a GET request (API is GET-only).
6. Return JSON result; optionally summarize if `verbose=False`.

## Key decisions
- Base URL: `https://dailymed.nlm.nih.gov/dailymed/services/v2`
- HTTPS required (HTTP disabled since 2016).
- Format extension must be in the URL path, not a query parameter.
- SPL SETID is a UUID-format identifier for a specific drug product.

## Caveats
- DailyMed API is GET-only; no POST or PUT.
- Large listings (all drug names) can return thousands of records — use query parameters to filter.
- XML format responses require an XML parser for downstream processing.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
