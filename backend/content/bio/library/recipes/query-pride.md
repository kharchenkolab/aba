---
name: query-pride
description: Query the PRIDE proteomics repository for mass spectrometry datasets and peptide evidence
when_to_use: When searching for publicly available proteomics experiments, protein identifications, or peptide spectral data at PRIDE/EBI
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [PRIDE, proteomics, mass spectrometry, peptide, protein, EBI, repository, LC-MS]
produces: [project records, assay metadata, protein identifications, peptide evidence]
domain: database
source: biomni:tool/database.py::query_pride
---
# Query PRIDE

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct endpoint URL.
2. If prompt given, use an LLM with the PRIDE schema to produce a full endpoint URL and description.
3. Normalize endpoint: prepend `https://www.ebi.ac.uk/pride/ws/archive/v2` if not already a full URL.
4. GET with params (default `pageSize=max_results`, `page=0`); parse JSON.
5. Return full result.

## Key decisions
- Base URL: `https://www.ebi.ac.uk/pride/ws/archive/v2`.
- Key endpoints: `projects` (search by keyword/species/disease/tissue), `assays`, `files`, `proteins`, `peptideevidences`.
- Search filters: `keyword`, `species`, `tissue`, `disease` query params.
- Pagination: `page` (0-indexed) and `pageSize`.
- Result structure includes `PagingObject` (total, page) and list of records.

## Caveats
- No authentication required for public data access.
- Large protein/peptide evidence queries can be slow; limit page size for interactive use.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
