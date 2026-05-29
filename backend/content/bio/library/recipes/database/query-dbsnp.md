---
name: query-dbsnp
description: Query NCBI dbSNP for SNPs and genetic variants using natural language or structured search terms
when_to_use: When looking up SNP rsIDs, allele frequencies, clinical significance, or variants in a gene or genomic region
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [SNP, variant, dbSNP, rsID, polymorphism, allele frequency, clinical significance, NCBI, consequence, variant consequence, population frequency, populations, minor allele frequency, MAF]
produces: [variant records, rsIDs, clinical significance, allele data]
domain: database
source: biomni:tool/database.py::query_dbsnp
---
# Query dbSNP

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or direct dbSNP search term.
2. If prompt given, use an LLM to translate it into a dbSNP NCBI eutils query string using field tags (e.g., `BRCA1[Gene Name]`, `rs6025[rs]`, `pathogenic[Clinical Significance]`).
3. POST to NCBI ESearch: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi` with `db=snp`, the search term, `retmode=json`, `usehistory=y`.
4. If hits found, fetch summaries via ESummary using `WebEnv` + `query_key` (or direct `id` list), with `retmax` capped at `max_results`.
5. Return total count, query interpretation, and formatted records.

## Key decisions
- Database name for eutils is `snp` (not `dbsnp`).
- `usehistory=y` enables WebEnv-based paging for large result sets; fall back to direct ID fetch if WebEnv not returned.
- LLM prompt instructs use of field tags: `[Gene Name]`, `[rs]`, `[Chromosome]`, `[Base Position]`, `[Clinical Significance]`, `[COMMON]`.
- Boolean operators must be uppercase: AND, OR, NOT.

## Caveats
- NCBI E-utilities rate-limit unauthenticated calls to 3/s; add `api_key` param if available.
- ESummary for `snp` returns a nested JSON; parse `result` key and skip `uids` entry.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
