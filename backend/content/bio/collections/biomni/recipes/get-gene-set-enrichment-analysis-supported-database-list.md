---
name: get-gene-set-enrichment-analysis-supported-database-list
description: List all gene set enrichment analysis databases available through gseapy/Enrichr
when_to_use: Discover which pathway and annotation databases can be used as input to gene set enrichment analysis
requires_tools: [run_python]
capabilities_needed: [gseapy]
keywords: [GSEA, Enrichr, gene set, database list, pathway, annotation]
produces: [list of supported database names]
domain: genomics
source: biomni:tool/genomics.py::get_gene_set_enrichment_analysis_supported_database_list
---
# Get Gene Set Enrichment Analysis Supported Database List

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Call `gseapy.get_library_name()` to retrieve the full list of available Enrichr libraries.
2. Return the list directly; it can be filtered or displayed for the user to select a database for downstream enrichment analysis.

## Key decisions
- No parameters needed; the list is fetched from the Enrichr API.
- Common user-friendly aliases: `"pathway"` → KEGG_2021_Human, `"ontology"` → GO_Biological_Process_2021, `"transcription"` → ChEA_2016, `"celltypes"` → PanglaoDB_Augmented_2021.

## Caveats
- Requires internet access to the Enrichr API.
- The library list changes as Enrichr adds new databases; cache the result if offline use is needed.

## In ABA
Implement with `run_python`; `ensure_capability("gseapy")`. Original impl: `biomni:tool/genomics.py::get_gene_set_enrichment_analysis_supported_database_list` — lift to lakeFS later.
