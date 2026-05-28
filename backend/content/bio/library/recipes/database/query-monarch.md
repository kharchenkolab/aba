---
name: query-monarch
description: Query the Monarch Initiative API for gene-disease-phenotype associations and semantic similarity
when_to_use: When exploring phenotype-gene relationships, HPO-annotated diseases, cross-species phenotype comparisons, or disease similarity via Monarch
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [Monarch Initiative, phenotype, HPO, MONDO, gene-disease, semantic similarity, biolink, association]
produces: [associations, entity records, phenotype profiles, similarity scores]
domain: database
source: biomni:tool/database.py::query_monarch
---
# Query Monarch Initiative

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct URL endpoint.
2. If prompt given, use an LLM with the Monarch API schema to produce a `url` (full URL with path params) and a `params` dict.
3. Append `limit=N` to the URL if not already present.
4. GET the URL via requests; parse JSON response.
5. Optionally condense result when verbose is off.

## Key decisions
- Disease IDs: MONDO ontology (e.g., `MONDO:0007947`).
- Gene IDs: HGNC (e.g., `HGNC:1100` for BRCA1).
- Phenotype IDs: HPO (e.g., `HP:0002616`).
- Association category strings follow biolink model (e.g., `biolink:GeneToPhenotypicFeatureAssociation`).
- Key patterns: search (`/search?q=...&category=biolink:Disease`), entity (`/entity/MONDO:0007947`), associations (`/entity/{id}/biolink:DiseaseToPhenotypicFeatureAssociation`), semantic similarity (`/semsim/compare`).

## Caveats
- Base URL is `https://api.monarchinitiative.org/v3/api`.
- The `limit` param controls pagination; default is 20.
- Semantic similarity endpoints accept comma-separated HPO term lists.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
