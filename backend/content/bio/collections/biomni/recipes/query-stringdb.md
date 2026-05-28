---
name: query-stringdb
description: Query the STRING protein-protein interaction database for interaction networks and scores
when_to_use: When you need protein interaction networks, interaction scores, or functional enrichment from STRING
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [string, protein interaction, PPI, network, interaction score, coexpression, enrichment]
produces: [interaction network data, interaction scores, enrichment results, network images]
domain: database
source: "biomni:tool/database.py::query_stringdb"
---
# Query STRING DB

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://version-12-0.string-db.org/api`
2. URL pattern: `{base}/{output_format}/{method}?{params}`
3. Output formats: `json`, `tsv`, `tsv-no-header`, `image`, `svg`
4. Key methods:
   - `network`: GET network interactions for identifiers
   - `interaction_partners`: GET interaction partners above a score threshold
   - `get_string_ids`: resolve gene names to STRING IDs
   - `enrichment`: functional enrichment analysis for a gene set
5. Common params:
   - `identifiers`: comma-separated gene names or UniProt IDs
   - `species`: NCBI taxon ID (9606=human, 10090=mouse, 7227=fly, 4932=yeast)
   - `required_score`: 0–1000 (default 400; higher = more stringent)
   - `caller_identity`: set to a meaningful identifier for API tracking
6. For image endpoints, stream binary content; for JSON, parse normally

## Key decisions
- Resolve ambiguous names first with `get_string_ids` before network queries
- `required_score` of 700+ gives high-confidence interactions only
- Image endpoints need `stream=True` in requests.get

## Caveats
- Gene name resolution is species-specific; always include `species`
- Large identifier lists may time out; batch into groups of ~100
- Attribution required: cite STRING paper when publishing results

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
