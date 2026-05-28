---
name: annotate-celltype-scrna
description: LLM-assisted cell-type annotation of scRNA-seq clusters from marker genes
when_to_use: Clustered scRNA-seq AnnData with leiden in .obs; want per-cluster cell-type labels constrained to a controlled vocabulary
requires_tools: [run_python]
capabilities_needed: [scanpy, langchain-core, numpy, pandas]
keywords: [cell type annotation, marker genes, leiden, ontology, scRNA-seq, single cell, LLM, CZI census]
produces: ["per-cluster cell-type labels in .obs[cell_type]", "rationale in .obs[cell_type_reason]", "annotated.h5ad"]
domain: genomics
source: biomni:tool/genomics.py::annotate_celltype_scRNA
---
# Annotate scRNA-seq cell types from cluster markers (LLM-assisted)

Distilled from a biomni implementation. In ABA, implement with the libraries
below — not biomni.

## Approach
1. Load the clustered AnnData (`sc.read_h5ad`); expects `leiden` in `.obs`.
2. Rank marker genes: `sc.tl.rank_genes_groups(adata, groupby="leiden", method="wilcoxon", use_raw=False)`; extract top 20 names and scores via `pd.DataFrame(adata.uns["rank_genes_groups"]["names"]).head(20)`. Keep only positive-score genes per cluster.
3. Load CZI census cell-type vocabulary from a reference parquet (`czi_census_datasets_v4.parquet`); build a set of valid ontology names from the `cell_type` column (semicolon-delimited entries).
4. Build a `langchain-core` prompt template that includes `data_info` (tissue/species context), the cluster's enriched gene list, and optionally a transferred-label composition table.
5. Per cluster, invoke the LLM (`chain.invoke({"cluster_info": ...})`); parse the `"name; score; reason"` response. If the predicted cell-type name is not in the CZI vocabulary, retry with a corrective note appended to the prompt. If the format is malformed, retry with a format reminder.
6. Map cluster IDs to validated cell-type names; write `adata.obs["cell_type"]` and `adata.obs["cell_type_reason"]`.
7. Save to `{data_dir}/annotated.h5ad` (gzip compression).

## Key decisions
- Wilcoxon differential expression; top-20 genes per cluster; only positive-score genes used.
- Cell-type labels must pass membership check against the CZI census ontology set (case-insensitive fallback).
- Transferred label composition included in prompt but explicitly distrusted when proportion < 0.5.
- Retry loop until a valid ontology label and well-formed response are produced.
- Output format enforced: `"name; score; reason"` (no leading number or space before score).

## Caveats
- Requires `czi_census_datasets_v4.parquet` as a reference dataset in the ABA data lake; register it as an ABA reference asset.
- LLM labels are hypotheses — validate against canonical marker literature.
- Leiden clustering must have been run before calling this recipe.
- Retry loop has no hard cap in the original code; add a max-retry guard in ABA.

## In ABA
Implement with `scanpy` + ABA's configured LLM via `langchain-core`. Load the CZI vocabulary from the registered reference dataset. `ensure_capability(scanpy, langchain-core)`. The `composition` argument (optional transferred labels) may come from the `unsupervised-celltype-transfer-between-scrna-datasets` recipe.
