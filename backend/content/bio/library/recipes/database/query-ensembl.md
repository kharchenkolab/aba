---
name: query-ensembl
description: Query the Ensembl REST API for gene/variant lookup, sequence retrieval, and genomic overlaps
when_to_use: When looking up gene coordinates, rsID-to-coordinate conversion, overlapping features, or sequence for Ensembl-supported species
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [Ensembl, gene lookup, variant, rsID, rs number, SNP, genomic region, sequence, VEP, variant consequence, consequence, missense, amino acid change, transcript, population frequency, homo_sapiens]
produces: [gene records, variant coordinates, sequences, overlapping features, transcript info]
domain: database
source: biomni:tool/database.py::query_ensembl
---
# Query Ensembl

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct endpoint path / full URL.
2. If prompt given, use an LLM with the Ensembl schema to produce an endpoint path and params dict.
3. Strip leading slash; construct full URL as `https://rest.ensembl.org/{endpoint}`.
4. GET with headers `Content-Type: application/json` and `Accept: application/json`, passing params as query string.
5. Return JSON result; optionally condense if verbose is off.

## Key decisions
- Region queries max 4,900,000 bp; split larger ranges if needed.
- Symbol lookup format: `lookup/symbol/{species}/{symbol}` (e.g., `lookup/symbol/homo_sapiens/BRCA2`).
- rsID-to-coordinates: `variation/homo_sapiens/{rsid}`.
- Overlapping genes: `overlap/region/homo_sapiens/{chrom}:{start}-{end}?feature=gene`.
- Assembly band coordinates: `info/assembly/homo_sapiens/{chrom}?bands=1`.
- Sequence: `sequence/id/{ensembl_id}?type=genomic|cdna|cds|protein`.

## Caveats
- Ensembl REST has rate limits; for bulk queries use POST endpoints or add delays.
- Species names use underscores: `homo_sapiens`, not `Homo sapiens`.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
