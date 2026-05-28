---
name: query-emdb
description: Query the Electron Microscopy Data Bank (EMDB) for 3D macromolecular EM structures
when_to_use: When searching cryo-EM or electron tomography structures, filtering by resolution, specimen type, or retrieving a specific EMDB entry
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [EMDB, cryo-EM, electron microscopy, structural biology, ribosome, resolution, macromolecule]
produces: [EMDB entry metadata, search results, resolution data, JSON API response]
domain: database
source: biomni:tool/database.py::query_emdb
---
# Query EMDB (Electron Microscopy Data Bank)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a natural language `prompt` or a direct `endpoint`.
2. If `prompt`: use an LLM with the EMDB API schema to produce a JSON with `endpoint`, `params`, and `description`.
   - System prompt instructs the LLM as a structural biology expert.
   - Key endpoints: `search` (with resolution/specimen filters), `entry/EMD-XXXXX` for specific entries.
3. Normalize the endpoint: if relative, prepend `https://www.ebi.ac.uk/emdb/api`.
4. Issue a GET request with any extracted `params`.
5. Return the JSON result; optionally summarize if `verbose=False`.

## Key decisions
- Base URL: `https://www.ebi.ac.uk/emdb/api`
- Resolution filters use `resolution_low` / `resolution_high` parameters.
- Direct entry format: `entry/EMD-XXXXX`.
- LLM response includes a `params` dict for GET query parameters (unlike other DB tools that embed params in the URL).

## Caveats
- EMDB IDs use the format EMD-##### (five or more digits).
- Some metadata fields (fitted PDB models, author lists) are nested and may require follow-up queries.
- Large search result pages need pagination via offset/page parameters.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
