---
name: query-clinvar
description: Query NCBI ClinVar for clinical variant classifications and pathogenicity data
when_to_use: When you need pathogenicity classifications, clinical significance, or variant-disease associations from ClinVar
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [clinvar, variant, pathogenic, clinical significance, SNP, VCV, RCV, NCBI, disease]
produces: [variant records, clinical significance, condition associations]
domain: database
source: "biomni:tool/database.py::query_clinvar"
---
# Query ClinVar

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Uses NCBI E-utilities (ESearch + ESummary) with `db=clinvar`
2. Step 1 — ESearch: GET `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi`
   - Params: `db=clinvar`, `term={search_term}`, `retmode=json`, `retmax=100`, `usehistory=y`
   - Returns: `esearchresult` with `count`, `idlist`, `webenv`, `querykey`
3. Step 2 — ESummary: GET `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi`
   - Params: `db=clinvar`, `query_key={qk}`, `WebEnv={webenv}`, `retmode=json`, `retmax=N`
   - Returns: `result` dict keyed by variant ID with full record summaries
4. ClinVar search syntax tags:
   - `[gene]`: gene symbol (e.g. `BRCA1[gene]`)
   - `[clinsig]`: clinical significance (e.g. `clinsig_pathogenic[prop]`)
   - `[rsid]`: dbSNP RS number (e.g. `rs6025[rsid]`)
   - `[dis]`: disease name
   - `[chr]` + `[chrpos37]`: genomic coordinates
   - `[prop]`: properties like `origin_germline`
   - Combine with `AND`, `OR`, `NOT`

## Key decisions
- Use `usehistory=y` and WebEnv/query_key to paginate large result sets
- For pathogenicity of a specific variant: `{gene}[gene] AND {variant}` is sufficient

## Caveats
- ClinVar IDs are VCV (variant-level) and RCV (condition-level); ESummary returns VCV records
- `clinsig_pathogenic[prop]` vs `clinsig_likely_pathogenic[prop]` are separate tags

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
