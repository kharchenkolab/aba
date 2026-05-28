---
name: region-to-ccre-screen
description: Retrieve candidate cis-Regulatory Elements (cCREs) overlapping a given genomic region from the SCREEN database
when_to_use: When identifying regulatory elements (promoters, enhancers, CTCF-binding sites) within a specific chromosomal interval
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [cCRE, SCREEN, regulatory elements, enhancer, promoter, CTCF, DNase, ENCODE, genomic region, epigenomics]
produces: [cCRE accessions, z-scores for DNase/CTCF/enhancer/promoter, histone mark maxima, proximal/distal classification]
domain: database
source: biomni:tool/database.py::region_to_ccre_screen
---
# Region to cCRE (SCREEN)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `coord_chrom` (e.g., "chr12"), `coord_start`, `coord_end` (integers), and `assembly` (default "GRCh38").
2. POST to `https://screen-beta-api.wenglab.org/dataws/cre_table` with JSON body containing `assembly`, `coord_chrom`, `coord_start`, `coord_end`.
3. Check response for HTTP errors or `"errors"` key in JSON.
4. From the `"cres"` list, extract and round key fields per cCRE:
   - Coordinates: `chrom`, `start`, `len`, `pct`
   - Z-scores: `ctcf_zscore`, `dnase_zscore`, `enhancer_zscore`, `promoter_zscore`
   - Metadata: `accession`, `isproximal`, `concordant`
   - Histone mark maxima: `ctcfmax`, `k4me3max`, `k27acmax`
5. Sort results by `dnase_zscore` descending.
6. Format and return as a readable string with one block per cCRE.

## Key decisions
- API endpoint: `https://screen-beta-api.wenglab.org/dataws/cre_table` (POST).
- Results sorted by DNase Z-score (highest = most accessible).
- All float scores rounded to 2 decimal places to reduce output size.
- Returns empty message if no cCREs overlap the region.

## Caveats
- Supports GRCh38 and mm10 assemblies; verify assembly string format.
- Very large genomic windows (>1 Mb) may return hundreds of cCREs.
- SCREEN beta API endpoint may change; check wenglab.org for updates.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
