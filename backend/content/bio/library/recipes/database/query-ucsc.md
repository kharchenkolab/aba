---
name: query-ucsc
description: Query the UCSC Genome Browser REST API for sequences, track data, and genome annotations
when_to_use: When retrieving genomic sequences, chromosome lists, track data, or assembly info from UCSC for any supported genome assembly
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [UCSC, genome browser, sequence, genomic coordinates, hg38, mm39, assembly, track]
produces: [DNA sequence, track records, chromosome lists, genome metadata]
domain: database
source: biomni:tool/database.py::query_ucsc
---
# Query UCSC Genome Browser

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct full URL endpoint.
2. If prompt given, use an LLM with the UCSC API schema to produce a `full_url` (base `https://api.genome.ucsc.edu`) and a description.
3. GET the constructed URL via requests, parse JSON response.
4. If `verbose=False`, condense results by trimming long lists and deep nesting.

## Key decisions
- Chromosome names require `chr` prefix (e.g., `chrM`, `chr1`).
- Positions are 0-based.
- Key endpoint patterns: `getData/sequence?genome=hg38&chrom=chrM&start=0&end=100`, `list/chromosomes?genome=hg38`, `list/ucscGenomes`.
- Common genome assemblies: `hg38` (human), `mm39` (mouse), `danRer11` (zebrafish).
- `maxItemsOutput` parameter limits large payloads.

## Caveats
- Some endpoints return large JSON blobs; use `maxItemsOutput` or slice ranges.
- The API does not require authentication.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
