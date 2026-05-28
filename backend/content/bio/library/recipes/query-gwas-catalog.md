---
name: query-gwas-catalog
description: Query the NHGRI-EBI GWAS Catalog for genome-wide association studies, signals, and trait associations
when_to_use: When searching for GWAS studies, associated SNPs, effect sizes, or EFO trait mappings
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [GWAS, genome-wide association, SNP, trait, EFO, association, p-value, odds ratio, NHGRI, EBI]
produces: [study records, associations, SNP-trait pairs, EFO trait metadata]
domain: database
source: biomni:tool/database.py::query_gwas_catalog
---
# Query GWAS Catalog

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct endpoint string.
2. If prompt given, use an LLM with the GWAS Catalog schema to produce an `endpoint` name (e.g., `studies`, `associations`, `singleNucleotidePolymorphisms`) and a `params` dict.
3. Strip leading slash; build URL as `https://www.ebi.ac.uk/gwas/rest/api/{endpoint}`.
4. GET with params via requests; parse HAL-based JSON.
5. Return full result.

## Key decisions
- Common endpoints: `studies`, `associations`, `singleNucleotidePolymorphisms`, `efoTraits`.
- Filter by trait using EFO IDs when possible (e.g., `?diseaseTraitId=EFO_0001360`).
- Pagination via `size` (page size) and `page` (0-indexed) parameters.
- P-value filtering via `pvalueMax` parameter.
- Response uses HAL `_embedded` wrapper; results are under `_embedded.{endpoint}`.

## Caveats
- The API is HAL-based REST; navigate `_links.next` for pagination.
- Trait names with spaces should be URL-encoded when building manual queries.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
