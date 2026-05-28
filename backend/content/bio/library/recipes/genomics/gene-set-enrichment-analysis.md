---
name: gene-set-enrichment-analysis
description: Over-representation / enrichment analysis for a gene list against Enrichr databases (GO, KEGG, ChEA, GWAS, cell types, kinases)
when_to_use: Given a ranked or unranked gene list (e.g. DE genes, cluster markers), identify enriched biological processes, pathways, transcription factors, disease associations, or cell-type signatures
requires_tools: [run_python]
capabilities_needed: [gget]
keywords: [enrichment, ORA, go terms, kegg, pathway, gene ontology, gsea, enrichr, over-representation]
produces: [ranked enrichment table with p-value, adjusted p-value, z-score, combined score, overlapping genes]
domain: genomics
source: biomni:tool/genomics.py::gene_set_enrichment_analysis
---
# Gene Set Enrichment Analysis (ORA via Enrichr)

Distilled from a biomni implementation. In ABA, implement with the libraries
below — not biomni.

## Approach
1. Accept a list of gene symbols, a `database` alias, optional `background_list`, and `top_k` (default 10).
2. Map the friendly `database` alias to the Enrichr collection name (e.g. `'ontology'` → `GO_Biological_Process_2021`, `'pathway'` → `KEGG_2021_Human`, `'transcription'` → `ChEA_2016`, `'diseases_drugs'` → `GWAS_Catalog_2019`, `'celltypes'` → `PanglaoDB_Augmented_2021`, `'kinase_interactions'` → `KEA_2015`).
3. Call `gget.enrichr(genes, database=database, background_list=background_list, plot=False)` — returns a DataFrame sorted by combined score.
4. Slice to `df.head(top_k)`.
5. Format each row: rank, path_name, p_val, z_score, combined_score, overlapping_genes, adj_p_val, database.
6. Optionally pass `plot=True` to gget to generate a bar chart in the working directory.

## Key decisions
- Uses `gget.enrichr`, which POSTs to the Enrichr REST API — requires network access.
- Results are already sorted by combined score (rank 1 = most enriched); `top_k` is just a head-slice.
- Background list narrows the universe; omit for default Enrichr universe.
- `combined_score = z_score × log(p_val)` (Enrichr definition) is the primary sort key.

## Caveats
- Enrichr is a web service; results depend on network availability and Enrichr uptime.
- Gene symbols must match the Enrichr namespace (human HGNC symbols by default).
- No multiple-testing correction across databases; adj_p_val is within-database only.
- `gget.enrichr` database name strings change between gget versions; verify against `gget.info()` if results are empty.
- ORA (over-representation) is not the same as GSEA (running-sum statistics on ranked lists); use this for unranked or threshold-based gene sets.

## In ABA
Implement with `gget`; `ensure_capability("gget")`. Original impl: `biomni:tool/genomics.py::gene_set_enrichment_analysis` → lift to lakeFS later.
