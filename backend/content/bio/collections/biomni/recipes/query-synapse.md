---
name: query-synapse
description: Search Synapse (Sage Bionetworks) for shared biomedical datasets, files, and folders
when_to_use: When finding genomics, clinical, or drug-screening datasets hosted on Synapse, including checking access restrictions
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [Synapse, Sage Bionetworks, biomedical datasets, genomics, cancer, Alzheimer, data sharing, access control]
produces: [dataset metadata, file listings, access restriction flags, JSON search hits]
domain: database
source: biomni:tool/database.py::query_synapse
---
# Query Synapse (Sage Bionetworks)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `prompt`, `query_term` (str or list), `query_type` (dataset/file/folder), and `max_results`.
2. If `prompt` without `query_term`: use an LLM to extract 1-2 search terms and determine `query_type` and `max_results`.
   - LLM returns JSON: `{"query_term": [...], "query_type": "...", "max_results": N}`.
   - Strip hyphens and special characters from terms; use simple, core terms only (AND logic between multiple terms).
3. POST to `https://repo-prod.prod.sagebase.org/repo/v1/search` with payload:
   - `queryTerm`, `returnFields`, `start`, `size`, `booleanQuery` (filtering by node_type).
4. For each result hit with an `id`, GET `/repo/v1/entity/{id}/accessRequirement` to check if access is restricted.
5. Annotate each hit with `access_restricted: true/false`.
6. Optionally summarize if `verbose=False`.

## Key decisions
- Authentication: Bearer token from `SYNAPSE_AUTH_TOKEN` env var (optional; omit for public datasets).
- Multiple `query_term` entries are combined with AND — use 1-2 terms for broader results.
- `query_type` values: `dataset`, `file`, `folder`.
- Default `return_fields`: `["name", "node_type", "description"]`.

## Caveats
- Private datasets require a valid Synapse auth token.
- Very broad single terms may return hundreds of results; use `max_results` to cap.
- Access requirement check adds one HTTP call per result hit.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
