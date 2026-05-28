---
name: query-unichem
description: Query UniChem 2.0 to cross-reference chemical identifiers across ChEMBL, DrugBank, PubChem, ChEBI, and other sources
when_to_use: When mapping a compound between chemical databases using InChI, InChIKey, or source-specific IDs
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [UniChem, chemical cross-reference, InChI, InChIKey, ChEMBL, DrugBank, PubChem, ChEBI, compound mapping]
produces: [cross-database compound IDs, source listings, connectivity results, JSON API response]
domain: database
source: biomni:tool/database.py::query_unichem
---
# Query UniChem 2.0

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a `prompt` or a direct `endpoint`.
2. If `prompt`: use an LLM with the UniChem API schema to produce `endpoint`, HTTP `method`, `data` (POST body), and `description`.
   - Valid identifier types: `uci`, `inchi`, `inchikey`, `sourceID`.
   - POST endpoints: `/compounds` (compound search), `/connectivity` (structure connectivity).
   - GET endpoint: `/sources` (list all source databases).
3. Normalize `endpoint` to full URL by prepending `https://www.ebi.ac.uk/unichem/api/v1`.
4. Issue GET or POST request accordingly (POST sends `data` as JSON body).
5. Return the result; optionally summarize if `verbose=False`.

## Key decisions
- Base URL: `https://www.ebi.ac.uk/unichem/api/v1`
- Compound searches and connectivity searches use POST; source listing uses GET.
- Common source IDs: 1=ChEMBL, 2=DrugBank, 5=PubChem, 7=ChEBI.
- Connectivity search accepts optional `searchComponents: true` for component-level matching.

## Caveats
- UniChem v1 API is distinct from the older v0/beta endpoints; always use v1.
- InChI strings can be long; ensure proper JSON body encoding, not URL encoding.
- Connectivity searches may return large result sets for common scaffolds.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
