---
name: query-encode
description: Query the ENCODE Portal API for functional genomics experiments, files, and biosamples
when_to_use: When finding ChIP-seq, RNA-seq, ATAC-seq, DNase-seq, or other functional genomics data from ENCODE for specific cell types, tissues, or targets
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [ENCODE, ChIP-seq, RNA-seq, ATAC-seq, DNase-seq, functional genomics, CTCF, K562, cell line, biosample, bigWig, BAM]
produces: [experiment metadata, file download URLs, biosample records, JSON search results]
domain: database
source: biomni:tool/database.py::query_encode
---
# Query ENCODE Portal API

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept either a `prompt` or a direct `endpoint`; `max_results` can be an integer or "all".
2. If `prompt`: use an LLM with the ENCODE Portal API schema to produce `full_url`, `description`, `data_type`, and `search_strategy`.
   - Keep queries simple: 1-3 parameters maximum for reliable results.
   - Use `searchTerm` for text-based searches; `type` to filter entity class.
   - Common types: `Experiment`, `File`, `Biosample`, `Dataset`.
3. Normalize endpoint: prepend `https://www.encodeproject.org` if relative.
4. Ensure `format=json` in the URL for search endpoints.
5. Append `limit={max_results}` to search endpoints if not present (use "all" for > 100 results).
6. Issue GET request. On 404, retry with a simplified fallback query if recognizable pattern (e.g., transcription factor search).
7. Attach `data_location_info` metadata to successful results.

## Key decisions
- Base URL: `https://www.encodeproject.org`
- Main search: `/search/?type=Experiment&assay_title=ChIP-seq&format=json`.
- Common assay titles: ChIP-seq, RNA-seq, ATAC-seq, DNase-seq, WGBS, Hi-C, CAGE.
- Common file formats: bam, fastq, bigWig, bigBed, bed, narrowPeak, broadPeak.
- `limit=all` for comprehensive retrieval (may be slow for large collections).

## Caveats
- ENCODE search is most reliable with 1-3 simple parameters; complex filter combinations may 404.
- File download URLs are relative paths; prepend `https://www.encodeproject.org` to access files.
- Some experiments require ENCODE DCC authentication for restricted-access data.

## In ABA
Implement with `run_python` using `requests`. `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
