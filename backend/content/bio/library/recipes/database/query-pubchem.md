---
name: query-pubchem
description: Query the PubChem PUG-REST API for chemical compound data by name, CID, or property
when_to_use: When retrieving molecular properties, synonyms, SMILES, InChI, or structure images for chemical compounds
requires_tools: [run_python]
capabilities_needed: [requests, time]
keywords: [PubChem, chemistry, molecular weight, SMILES, InChI, CID, compound properties, drug]
produces: [compound properties, synonyms, structure data, molecular formula, JSON or text response]
domain: database
source: biomni:tool/database.py::query_pubchem
---
# Query PubChem PUG-REST

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a natural language `prompt` or a direct `endpoint` URL.
2. If `prompt`: use an LLM with the PubChem PUG-REST schema to produce `full_url` and `description`.
   - Base URL: `https://pubchem.ncbi.nlm.nih.gov/rest/pug`.
   - Use `compound/name/{name}/` for chemical names, `compound/cid/{cid}/` for PubChem IDs.
   - Use CSV format for multiple properties, TXT for single, PNG for images.
3. If direct `endpoint`: normalize to full URL.
4. Enforce rate limiting: max 5 requests per second (sleep if needed using `time`).
5. Issue a GET request and return the result.

## Key decisions
- Rate limit: 5 req/s — track last request time and sleep the remainder of the 0.2 s interval.
- Output format is embedded in the URL path (`.../property/MolecularWeight/txt`).
- Common operations: `property`, `synonyms`, `record`, `xrefs`, `PNG` image.

## Caveats
- PubChem throttles heavy usage; respect the 5 req/s limit.
- Name-to-CID lookup may return multiple matches; use CID for exact retrieval.
- PNG image endpoints return binary data, not JSON.

## In ABA
Implement with `run_python` using `requests` and `time`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
