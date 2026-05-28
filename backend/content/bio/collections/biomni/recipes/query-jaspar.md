---
name: query-jaspar
description: Query the JASPAR database for transcription factor binding site (TFBS) matrices
when_to_use: When you need transcription factor PWMs, binding profiles, or JASPAR matrix metadata
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [jaspar, transcription factor, TFBS, PWM, binding site, motif, matrix, regulatory]
produces: [TF binding matrices, PWM data, TF metadata, collection listings]
domain: database
source: "biomni:tool/database.py::query_jaspar"
---
# Query JASPAR

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://jaspar.elixir.no/api/v1`
2. All endpoint paths should end with `/`
3. Key endpoints:
   - Matrix by ID: GET `{base}/matrix/{matrix_id}/` (e.g. `MA0002.2`)
   - Search matrices: GET `{base}/matrix/?search={tf_name}&tax_group={group}&collection={coll}`
   - All matrices: GET `{base}/matrix/?format=json`
   - TF info: GET `{base}/taxon/{taxon_id}/`
4. Matrix ID format: `MA####.#` (e.g. `MA0002.2`)
5. Taxonomic groups: `vertebrates`, `plants`, `fungi`, `insects`, `nematodes`, `urochordates`
6. Collections: `CORE`, `UNVALIDATED`, `PENDING`, `REDUNDANT`, `PHYLOFACTS`, `CNE`, `POLII`, `FAM`
7. Response JSON for matrix includes: `matrix_id`, `name`, `pfm` (position frequency matrix), `species`, `uniprot_ids`, `class`, `family`
8. Pagination via `?page=N&page_size=M` params

## Key decisions
- CORE collection contains the highest-quality, non-redundant matrices
- For sequence-based inference, use the `/infer/` endpoint with a protein sequence

## Caveats
- Matrix IDs are versioned; use latest version for current analyses
- PFM (position frequency matrix) values need normalization to get PWM

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
