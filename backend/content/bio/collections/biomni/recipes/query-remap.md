---
name: query-remap
description: Query the ReMap database for transcription factor ChIP-seq binding peaks and regulatory catalogues
when_to_use: When looking up TF binding sites, ChIP-seq peak catalogues, or regulatory regions for a specific TF, cell line, or genomic locus
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [ReMap, ChIP-seq, transcription factor, binding site, regulatory region, CTCF, enhancer, peak, biotype]
produces: [TF binding peaks, catalogue entries, regulatory region records]
domain: database
source: biomni:tool/database.py::query_remap
---
# Query ReMap

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct full endpoint URL.
2. If prompt given, use an LLM with the ReMap schema to produce a full endpoint URL and description.
3. Normalize endpoint: prepend `https://remap.univ-amu.fr/api/v1` if not a full URL.
4. GET the URL via requests; parse JSON.
5. Optionally condense when verbose is off.

## Key decisions
- Base URL: `https://remap.univ-amu.fr/api/v1`.
- Key endpoints: `catalogue/tf` (TF catalogue), `catalogue/biotype`, `browse/peaks` (binding peaks).
- Filter peaks by: `tf` (TF name), cell line, `biotype`, `chr`, `start`, `end`.
- Use `limit` param to cap result size (default 100).
- ReMap is based on public ChIP-seq experiments; data covers human and mouse.

## Caveats
- ReMap covers regulatory regions only (ChIP-seq peaks); it does not provide variant or expression data.
- No authentication required.
- Genomic coordinate queries must specify assembly-consistent coordinates.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
