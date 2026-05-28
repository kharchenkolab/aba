---
name: query-geo
description: SEARCH/discover NCBI GEO for studies & datasets by keyword (organism, condition, gene). Not for a known accession — see fetch-geo-processed-matrices.
when_to_use: To FIND studies/datasets in GEO when you do NOT yet have an accession — search by organism, condition, gene, or keyword (eutils esearch). Do NOT use this for a known GSE/GSM — to list a series' samples + per-sample metadata, or download its matrices, use fetch-geo-processed-matrices (it has the GEOparse sample table). This recipe's esearch only returns study-level records, not the per-sample roster.
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [GEO search, find study, discover dataset, gene expression, RNA-seq, microarray, query, GDS, NCBI, expression omnibus]
produces: [dataset metadata, series records, expression profile summaries]
domain: database
source: "biomni:tool/database.py::query_geo"
---
# Query GEO

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Uses NCBI E-utilities with `db=gds` (DataSets) or `db=geoprofiles` (expression profiles)
2. Step 1 — ESearch: GET `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi`
   - Params: `db={gds|geoprofiles}`, `term={search_term}`, `retmode=json`, `retmax=100`, `usehistory=y`
3. Step 2 — ESummary: GET `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi`
   - Params: `db={db}`, `query_key={qk}`, `WebEnv={webenv}`, `retmode=json`, `retmax=N`
4. GEO search syntax tags:
   - `[ETYP]`: entry type — `gse` (Series), `gds` (DataSet), `gpl` (Platform), `gsm` (Sample)
   - `[ORGN]`: organism (e.g. `Homo sapiens[ORGN]`, `Mus musculus[ORGN]`)
   - `[PDAT]`: publication date (e.g. `2020[PDAT]`, or range `2015/01:2020/12[PDAT]`)
   - `[Title]`: title words
   - `[Gene Symbol]`: gene symbol (for geoprofiles db)
   - Combine with `AND`, `OR`, `NOT`
5. Database choice:
   - `gds`: for finding experiments/series (GSE, GDS, GPL, GSM records) — default
   - `geoprofiles`: for gene-level expression data across samples

## Key decisions
- Default to `gds` database for most study-level queries
- Include `gse[ETYP]` to restrict to Series entries when looking for full experiments
- Add organism tag to avoid cross-species contamination

## Caveats
- GEO DataSets (GDS) are a curated subset of GSE; most data is in GSE series
- ESummary record for GDS includes `title`, `summary`, `taxon`, `gpl`, `n_samples`
- To download actual expression data, use GEO FTP or `GEOparse` library with GSE accession

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
