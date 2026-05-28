---
name: interspecies-gene-conversion
description: Convert Ensembl gene IDs between species using BioMart one-to-one ortholog mappings
when_to_use: Translate a list of Ensembl gene IDs from one organism to their homologous IDs in another organism (e.g., human to mouse) for cross-species analysis
requires_tools: [run_python]
capabilities_needed: [pybiomart, numpy, pandas]
keywords: [ortholog, gene conversion, interspecies, BioMart, Ensembl, homolog, cross-species]
produces: [CSV file with source and target gene ID columns]
domain: genomics
source: biomni:tool/genomics.py::interspecies_gene_conversion
---
# Interspecies Gene Conversion via BioMart

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate `source_species` and `target_species` against the supported species dict (human, mouse, rat, zebrafish, fly/drosophila, worm, yeast, chicken, pig, cow, dog, macaque); raise `ValueError` if unknown.
2. Resolve dataset name (e.g., `"hsapiens_gene_ensembl"`) and homolog attribute (e.g., `"mmusculus_homolog_ensembl_gene"`) from lookup tables.
3. Connect: `Dataset(name=source_dataset_name, host="http://www.ensembl.org")`.
4. Query: `source_dataset.query(attributes=["ensembl_gene_id", homolog_attribute], filters={"link_ensembl_gene_id": gene_list})`. If empty, fall back to fetching all mappings and filtering.
5. Drop rows where homolog column is NaN or empty; build `gene_mapping` dict.
6. Map each input gene — genes without homologs get `np.nan`.
7. Assert output length equals input length.
8. Save DataFrame `{source}_genes / {target}_genes` to `{source}_to_{target}_gene_conversion.csv`.

## Key decisions
- One-to-one ortholog attributes are used; one-to-many relationships are not resolved.
- Fallback query fetches the full table if the filtered query returns nothing (handles BioMart quirks).
- If both primary and fallback queries fail, NaN-filled output is still saved for traceability.

## Caveats
- Requires internet access to Ensembl BioMart.
- Only ENSEMBL gene IDs are supported as input (e.g., ENSG00000...).
- Many-to-one or many-to-many orthologs are dropped; check the output for NaN values.

## In ABA
Implement with `run_python`; `ensure_capability("pybiomart", "numpy", "pandas")`. Original impl: `biomni:tool/genomics.py::interspecies_gene_conversion` — lift to lakeFS later.
