---
name: query-openfda
description: Query the OpenFDA API for drug adverse events, recalls, labels, and enforcement actions
when_to_use: When investigating drug safety signals, adverse event frequencies, drug labels, or FDA enforcement actions
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [OpenFDA, adverse events, drug safety, FDA, recall, label, pharmacovigilance, MedDRA]
produces: [adverse event records, recall notices, drug labels, count tables]
domain: database
source: biomni:tool/database.py::query_openfda
---
# Query OpenFDA

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct endpoint URL.
2. If prompt given, use an LLM with the OpenFDA schema to generate a `full_url` including base `https://api.fda.gov` and query parameters.
3. Append `limit=N` to the URL if not present (max 1000 per call; pagination via `skip`).
4. GET the URL via requests; parse JSON response.
5. Optionally condense when verbose is off.

## Key decisions
- Key endpoints: `/drug/event.json` (adverse events), `/drug/label.json` (labels), `/drug/recall.json` (recalls).
- Search syntax: `search=field:term`; AND: `+AND+`; OR: space-separated; exact phrase: `"quoted"`.
- Count unique values with `count=field.exact` (no `search` needed).
- Pagination: `skip=N` (max 25000), `limit=M`.
- Common fields: `patient.drug.medicinalproduct`, `patient.reaction.reactionmeddrapt.exact`, `receivedate`.

## Caveats
- OpenFDA returns `{"meta": ..., "results": [...]}` structure; results are under `results` key.
- Some count queries return `{"term": ..., "count": ...}` pairs, not full records.
- API is public, no key needed, but rate limits apply at high volume.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
