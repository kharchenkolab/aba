---
name: query-regulomedb
description: Query RegulomeDB for regulatory evidence at a human genomic variant or coordinate
when_to_use: When assessing whether a SNP or genomic position overlaps regulatory elements such as TF binding sites, chromatin accessibility, or eQTLs
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [RegulomeDB, regulatory, SNP, rsID, ChIP-seq, DNase, eQTL, regulatory score, non-coding variant]
produces: [regulatory score, evidence tracks, nearby features, regulatory category]
domain: database
source: biomni:tool/database.py::query_regulomedb
---
# Query RegulomeDB

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct full endpoint URL.
2. If prompt given, use an LLM (no external schema needed) to extract an endpoint URL in the format `https://regulomedb.org/regulome-search/?regions={rsID_or_coords}&genome=GRCh38`.
3. For rsIDs: endpoint pattern `https://regulomedb.org/regulome-search/?regions=rs35675666&genome=GRCh38`.
4. For coordinates: pattern `https://regulomedb.org/regulome-search/?regions=chr11:5246919-5246919&genome=GRCh38`.
5. GET with header `Accept: application/json`; parse JSON.
6. Optionally condense result when verbose is off.

## Key decisions
- RegulomeDB is human-only (GRCh38 or GRCh37/hg19).
- Single-base queries use the same position for start and end.
- Chromosome names require the `chr` prefix.
- Thumbnail endpoints exist for ChIP and chromatin tracks; append `&thumbnail=chip` or `&thumbnail=chromatin`.

## Caveats
- The API returns a regulome score (1a–6) indicating strength of regulatory evidence; lower numbers = stronger evidence.
- No authentication required.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
