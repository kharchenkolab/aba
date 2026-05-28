---
name: get-rna-seq-archs4
description: Retrieve bulk RNA-seq tissue expression data for a gene from the ARCHS4 database via gget
when_to_use: Look up the top K tissues by median TPM for a human or mouse gene using ARCHS4 uniformly-processed RNA-seq data
requires_tools: [run_python]
capabilities_needed: [gget, pandas]
keywords: [ARCHS4, RNA-seq, tissue expression, TPM, bulk expression, gene expression]
produces: [ranked tissue expression table with median TPM values]
domain: genomics
source: biomni:tool/genomics.py::get_rna_seq_archs4
---
# Get RNA-seq Tissue Expression from ARCHS4

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Call `gget.archs4(gene_name, which="tissue")` to retrieve a DataFrame of tissue expression.
2. Check if the returned DataFrame is empty; report accordingly.
3. Iterate over the first `K` rows; extract `row["id"]` (tissue name) and `row["median"]` (median TPM).
4. Format and return a readable summary string listing tissue and median TPM for each entry.

## Key decisions
- `which="tissue"`: aggregates expression by tissue type; alternatives include `"cell_line"`.
- `K` (default 10): controls how many top tissues are reported.
- Results are already sorted by expression level in the gget response.

## Caveats
- Requires internet access to query the ARCHS4 API.
- Gene names must match ARCHS4 identifiers (HGNC symbols for human, MGI for mouse).
- Very lowly-expressed genes may return an empty DataFrame.

## In ABA
Implement with `run_python`; `ensure_capability("gget")`. Original impl: `biomni:tool/genomics.py::get_rna_seq_archs4` — lift to lakeFS later.
