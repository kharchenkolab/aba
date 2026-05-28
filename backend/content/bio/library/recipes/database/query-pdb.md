---
name: query-pdb
description: Search the RCSB Protein Data Bank for protein structures using the Search API
when_to_use: When you need to find PDB entries by keyword, attribute, sequence, or structure similarity
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [pdb, protein structure, crystallography, cryo-em, rcsb, structure search]
produces: [PDB entry identifiers, search result metadata]
domain: database
source: "biomni:tool/database.py::query_pdb"
---
# Query PDB (Search)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Search endpoint: POST `https://search.rcsb.org/rcsbsearch/v2/query`
2. Request body is a JSON query object with three main parts:
   - `query`: node tree (terminal or group)
   - `return_type`: `"entry"`, `"assembly"`, `"polymer_entity"`, `"polymer_instance"`, `"mol_definition"`
   - `request_options.paginate`: `{"start": 0, "rows": N}`
3. Terminal node types:
   - `full_text`: `{"type":"terminal","service":"full_text","parameters":{"value":"insulin"}}`
   - `text` (attribute search): requires `attribute`, `operator`, `value`
   - Operators: `exact_match`, `contains_words`, `less_or_equal`, `greater_or_equal`, `range`
4. Group nodes combine terminals: `{"type":"group","logical_operator":"and","nodes":[...]}`
5. Response JSON has `result_set` (list of `{identifier, score}`) and `total_count`

## Key decisions
- Use `full_text` for open-ended keyword searches
- Use `text` service with specific attributes (e.g. `rcsb_entry_info.resolution_combined`) for filtered searches
- Always set pagination to avoid huge responses

## Caveats
- Search API returns identifiers only; use `query_pdb_identifiers` to fetch detailed data
- `polymer_entity` identifiers have format `{PDBID}_{entity_number}` (e.g. `1ABC_1`)

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
