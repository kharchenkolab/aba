---
name: query-opentarget
description: Query the Open Targets Platform GraphQL API for gene-disease associations, drug targets, and evidence
when_to_use: When investigating therapeutic targets, disease-gene associations, or drug mechanisms via Open Targets
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [Open Targets, drug target, disease association, Alzheimer, EFO, Ensembl, GraphQL, pharmacogenomics]
produces: [target-disease associations, drug evidence, target scores, disease summaries]
domain: database
source: biomni:tool/database.py::query_opentarget
---
# Query Open Targets

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct GraphQL query string (+ optional variables dict).
2. If prompt given, use an LLM with the Open Targets GraphQL schema to produce a `query` string and `variables` JSON object.
3. POST to `https://api.platform.opentargets.org/api/v4/graphql` with body `{"query": ..., "variables": ...}` and header `Content-Type: application/json`.
4. Return the JSON result; optionally condense when verbose is off.

## Key decisions
- Disease IDs use EFO ontology (e.g., `EFO_0000249` for Alzheimer's disease).
- Target IDs use Ensembl gene IDs (e.g., `ENSG00000197386`).
- Always include `first: N` to limit result size (default ~10).
- Escape quotes inside GraphQL strings with `\"`.
- Variables dict is passed separately, not interpolated into the query string.

## Caveats
- The API is public and does not require authentication.
- Some queries (e.g., `associatedDiseases`) can return thousands of rows; use pagination cursors.
- LLM must generate valid GraphQL, not REST-style JSON; validate braces before sending.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
