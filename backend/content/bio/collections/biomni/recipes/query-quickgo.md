---
name: query-quickgo
description: Query the QuickGO API for Gene Ontology terms, annotations, and gene products
when_to_use: When searching GO terms by name or ID, retrieving GO term hierarchies, or finding GO annotations for genes or proteins
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [Gene Ontology, GO terms, QuickGO, GO annotations, biological process, molecular function, cellular component, gene product, EBI]
produces: [GO term records, annotation sets, gene product entries, term hierarchy, JSON API response]
domain: database
source: biomni:tool/database.py::query_quickgo
---
# Query QuickGO API

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a `prompt` or a direct `endpoint`; `max_results` capped at 100.
2. If `prompt`: use an LLM with the QuickGO API schema to produce a `full_url`.
   - Main services: `/ontology` (GO/ECO terms), `/annotation` (GO annotations), `/geneproduct`.
   - GO term search: `/ontology/go/search?query={term}&limit={n}`.
   - Specific term: `/ontology/go/terms/{GO:ID}`.
   - Term relationships: `/ontology/go/terms/{GO:ID}/children`, `/descendants`, `/ancestors`.
   - Annotations: `/annotation/search` with organism (taxon ID), evidence code, qualifier filters.
3. Normalize endpoint: prepend `https://www.ebi.ac.uk/QuickGO/services` if relative.
4. If `limit=` not in URL and not a direct terms lookup, append `?limit={max_results}` or `&limit={max_results}`.
5. Issue GET request and return result; optionally summarize if `verbose=False`.

## Key decisions
- Base URL: `https://www.ebi.ac.uk/QuickGO/services`
- Max `limit` per request: 100.
- Common taxon IDs: 9606 (human), 10090 (mouse), 7227 (fly).
- GO aspects: `biological_process`, `molecular_function`, `cellular_component`.
- Evidence codes: IEA (electronic), IDA, IPI, IMP, IGI (experimental).

## Caveats
- `max_results > 100` is silently capped to 100 by the API.
- Annotation searches can return large sets; always specify a limit or taxon filter.
- GO IDs must be formatted as `GO:XXXXXXX` (7 digits).

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
