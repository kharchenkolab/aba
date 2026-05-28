---
name: query-mpd
description: Query the Mouse Phenome Database (MPD) for mouse strain phenotype data
when_to_use: When looking up phenotypic measurements, strain comparisons, or trait data for laboratory mouse strains
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [mouse phenome, MPD, mouse strains, phenotype, C57BL/6J, inbred strains, Jackson Lab]
produces: [phenotype records, strain measurements, JSON API response]
domain: database
source: biomni:tool/database.py::query_mpd
---
# Query MPD (Mouse Phenome Database)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a natural language `prompt` or a direct `endpoint` URL.
2. If `prompt`: use an LLM to translate the query into a full MPD API endpoint URL.
   - System prompt instructs the LLM as a mouse-genetics expert with the MPD API schema.
   - LLM returns JSON with `endpoint` and `description` fields.
3. Normalize the endpoint: if relative, prepend `https://phenome.jax.org`.
4. Issue a GET request to the resolved URL.
5. Return the JSON result; optionally summarize if `verbose=False`.

## Key decisions
- Base URL: `https://phenome.jax.org`
- Common resource paths: `/api/strains`, `/api/measures`, `/api/genes`
- Common phenotypic domains: behavior, blood_chemistry, body_weight, cardiovascular, growth, metabolism
- Common strain names: C57BL/6J, DBA/2J, BALB/cJ, A/J, 129S1/SvImJ

## Caveats
- MPD may require strain names URL-encoded if they contain slashes (e.g., C57BL/6J).
- Not all endpoints are publicly documented; LLM schema lookup helps cover undocumented paths.
- Large result sets may need pagination.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
