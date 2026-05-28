---
name: query-reactome
description: Query the Reactome pathway database for pathway details, gene membership, and pathway diagrams
when_to_use: When retrieving biological pathway information, mapping genes to pathways, or downloading pathway diagrams from Reactome
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [Reactome, pathway, biological process, gene, R-HSA, ContentService, AnalysisService, diagram]
produces: [pathway records, gene-pathway mappings, diagram PNG files, enrichment results]
domain: database
source: biomni:tool/database.py::query_reactome
---
# Query Reactome

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct endpoint string.
2. If prompt given, use an LLM with the Reactome schema to produce an `endpoint`, a `base` (`content` or `analysis`), `params`, and optionally `download=true`.
3. Remap legacy `data/query/{gene}` endpoints to `/search/query?query={gene}&species=Homo+sapiens` against ContentService.
4. Build URL: `https://reactome.org/ContentService/{endpoint}` or `https://reactome.org/AnalysisService/{endpoint}`.
5. GET with params; if `download=true` and result contains `stId`/`dbId`, fetch diagram PNG from `/data/pathway/{id}/diagram` and save to `output_dir`.

## Key decisions
- ContentService: specific entity retrieval (`data/pathways/R-HSA-73894`, `search/query`).
- AnalysisService: gene-list over-representation analysis.
- Human pathway stable IDs start with `R-HSA-`.
- Gene queries use official gene symbol (e.g., `BRCA1`).

## Caveats
- The `data/query/` endpoint was deprecated; the impl redirects to `/search/query`.
- Diagram download requires an `output_dir`; PNG may be unavailable for some pathway IDs.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
