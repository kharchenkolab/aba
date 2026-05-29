---
name: query-gnomad
description: Query gnomAD for population variant frequencies and constraint metrics via GraphQL
when_to_use: When retrieving allele frequencies, variant consequence, or gene constraint scores from gnomAD
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [gnomAD, allele frequency, population genetics, variant, rsID, SNP, constraint, pLoF, missense, GRCh38, population frequency, populations, minor allele frequency, MAF, how common, variant consequence]
produces: [variant records, allele frequencies, constraint metrics, gene summaries]
domain: database
source: biomni:tool/database.py::query_gnomad
---
# Query gnomAD

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct gene symbol.
2. If prompt given, use an LLM with the gnomAD GraphQL schema to generate a complete GraphQL query string.
3. If gene symbol given directly, substitute it into a standard query template (the biomni impl replaces "BRCA1" placeholder with the target gene).
4. POST to `https://gnomad.broadinstitute.org/api` with body `{"query": query_str}` and header `Content-Type: application/json`.
5. Return JSON result; optionally condense for verbose=False.

## Key decisions
- Default reference genome: GRCh38; default dataset: `gnomad_r4`.
- The GraphQL API accepts a single `query` string (no separate `variables` dict needed for simple gene queries).
- Use official gene symbol (e.g., `BRCA1`), not aliases or full names.
- GraphQL query strings must escape internal quotes as `\"`.

## Caveats
- gnomAD GraphQL can return very large payloads for genes with many variants; consider querying specific variant ranges.
- The API is public and does not require authentication.
- Constraint scores (pLI, Z-scores) are at the gene level; individual variant frequencies are nested under `variants`.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
