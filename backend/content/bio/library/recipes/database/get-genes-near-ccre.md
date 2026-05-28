---
name: get-genes-near-ccre
description: Retrieve the k nearest genes to a given cCRE accession from the SCREEN database
when_to_use: When identifying candidate target genes for a regulatory element (cCRE) based on genomic proximity
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [cCRE, SCREEN, nearby genes, regulatory element, target gene, ENCODE, Ensembl, gene distance, epigenomics]
produces: [gene names, distances, Ensembl IDs, chromosomal coordinates of nearby genes]
domain: database
source: biomni:tool/database.py::get_genes_near_ccre
---
# Get Genes Near cCRE (SCREEN)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `accession` (ENCODE cCRE accession, e.g., "EH38E1516980"), `assembly` (e.g., "GRCh38"), `chromosome` (e.g., "chr12"), and `k` (top-k genes by distance, default 10).
2. POST to `https://screen-beta-api.wenglab.org/dataws/re_detail/nearbyGenomic` with JSON body: `{"accession": ..., "assembly": ..., "coord_chrom": ...}`.
3. Check for HTTP errors and `"errors"` key in response JSON.
4. Extract `nearby_genes` list from `response[accession]["nearby_genes"]`.
5. Sort genes by `distance` (ascending) and take the top `k`.
6. For each gene return: `name`, `distance`, `ensemblid_ver`, `chrom`, `start`, `stop`.

## Key decisions
- API endpoint: `https://screen-beta-api.wenglab.org/dataws/re_detail/nearbyGenomic` (POST).
- Results sorted by distance (closest first).
- Ensembl IDs include version suffix (`ensemblid_ver`).
- Returns a log string concatenating all steps and results.

## Caveats
- Accession format differs by assembly: EH38E* for GRCh38, EM10E* for mm10.
- "Nearby" is defined by SCREEN's genomic window; very isolated regions may return few genes.
- Distance is in base pairs; includes both upstream and downstream genes.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
