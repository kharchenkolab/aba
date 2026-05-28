---
name: query-cbioportal
description: Query cBioPortal for cancer genomics data including mutations, copy number, and expression
when_to_use: When you need cancer genomic data such as somatic mutations, CNV, expression profiles, or clinical data from TCGA or other studies
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [cbioportal, cancer genomics, TCGA, mutation, somatic, copy number, expression, clinical]
produces: [mutation records, molecular profiles, study metadata, patient data]
domain: database
source: "biomni:tool/database.py::query_cbioportal"
---
# Query cBioPortal

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://www.cbioportal.org/api`
2. REST API; responses are JSON arrays or objects
3. Key endpoints:
   - List studies: GET `{base}/studies?pageSize=100`
   - Study info: GET `{base}/studies/{study_id}`
   - Molecular profiles: GET `{base}/studies/{study_id}/molecular-profiles`
   - Mutations for gene: GET `{base}/molecular-profiles/{profile_id}/mutations?entrezGeneId={id}`
   - Gene panel data: GET `{base}/molecular-profiles/{profile_id}/gene-panel-data`
   - Clinical data: GET `{base}/studies/{study_id}/clinical-data`
   - Patients: GET `{base}/studies/{study_id}/patients`
4. Common study IDs: `brca_tcga`, `gbm_tcga`, `luad_tcga`, `coadread_tcga`, `prad_tcga`
5. Molecular profile ID pattern: `{study_id}_mutations`, `{study_id}_cna`, `{study_id}_mrna`
6. Gene identifiers: Hugo symbol (e.g. `BRCA1`) or Entrez ID (e.g. 672)
7. Pagination: `?pageNumber=0&pageSize=50`; use `projection=DETAILED` for richer data

## Key decisions
- Resolve Entrez IDs first via `{base}/genes/{hugo_symbol}` for downstream mutation queries
- Use `projection=DETAILED` to get full mutation annotation including protein change

## Caveats
- Some endpoints require POST with body for multi-gene/multi-sample queries
- TCGA study IDs are lower-case with underscores; verify exact IDs via `/studies`
- Rate limits apply; add delays for large batch queries

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
