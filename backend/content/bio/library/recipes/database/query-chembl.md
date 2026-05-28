---
name: query-chembl
description: Query the ChEMBL REST API for bioactivity, drug, molecule, and target data
when_to_use: When looking up drug bioactivity, IC50/Ki values, approved drugs, molecule structures, SMILES similarity, or mechanism-of-action data
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [ChEMBL, bioactivity, IC50, drug, SMILES, kinase, assay, target, mechanism, approved drugs]
produces: [molecule records, bioactivity data, assay results, drug metadata, JSON API response]
domain: database
source: biomni:tool/database.py::query_chembl
---
# Query ChEMBL REST API

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `prompt`, `endpoint`, `chembl_id`, `smiles`, or `molecule_name` (priority order).
2. If `chembl_id`: direct lookup at `/molecule/{chembl_id}.json`.
3. If `smiles`: similarity search at `/similarity/{smiles}/80.json` (80% cutoff default).
4. If `molecule_name`: full-text search at `/molecule/search.json?q={name}&limit={max}`.
5. If `prompt`: use an LLM with the ChEMBL API schema to produce `full_url`.
   - Falls back to keyword-based heuristics if LLM fails (binding targets → activity endpoint, etc.).
6. If direct `endpoint`: normalize to full URL.
7. Append `?limit={max_results}` or `&limit={max_results}` if not already present.
8. Issue a GET request and return the result.

## Key decisions
- Base URL: `https://www.ebi.ac.uk/chembl/api/data`
- All endpoints need `.json` suffix for JSON output (default is XML).
- Common filters: `max_phase=4` (approved), `assay_type=B/F/A`, `pchembl_value__gte=5`.
- Drug metadata sub-endpoints: `/drug`, `/drug_indication`, `/mechanism`, `/atc_class`.
- Use parent ChEMBL IDs for drug-level endpoints.

## Caveats
- SMILES for similarity/substructure must be raw (not URL-encoded twice).
- LLM fallback uses simple keyword matching which may produce broad results.
- Some endpoints (images) return SVG/binary, not JSON.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
