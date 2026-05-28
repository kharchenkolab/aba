---
name: query-kegg
description: Query the KEGG REST API for pathways, genes, compounds, and cross-database conversions
when_to_use: When you need KEGG pathway data, gene annotations, metabolic network information, or ID conversions between NCBI/UniProt and KEGG
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [kegg, pathway, metabolic network, gene, compound, reaction, drug, BRITE, orthology]
produces: [pathway entries, gene annotations, compound data, ID mappings]
domain: database
source: "biomni:tool/database.py::query_kegg"
---
# Query KEGG

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://rest.kegg.jp`
2. URL pattern: `{base}/{operation}/{argument}[/{argument2}...]`
3. Operations:
   - `info/{db}`: database statistics
   - `list/{db}[/{org}]`: list entries (e.g. `list/pathway/hsa` for human pathways)
   - `find/{db}/{keyword}`: text search
   - `get/{entry_id}`: fetch full record (e.g. `get/hsa:672` for human BRCA1)
   - `conv/{target_db}/{source_db}:{id}`: ID conversion (e.g. `conv/genes/ncbi-geneid:672`)
   - `link/{target_db}/{source_db}`: cross-link between databases
4. Organism codes: `hsa` (human), `mmu` (mouse), `dme` (fly), `sce` (yeast)
5. Responses are plain text (tab-separated); parse with `str.splitlines()` and split on tabs

## Key decisions
- Use organism-prefixed IDs for gene-level queries (e.g. `hsa:672` not just `672`)
- `find` returns tab-delimited ID-description pairs; `get` returns a KEGG flat-file record
- For pathway maps, `get/map{pathway_id}/kgml` returns machine-readable XML

## Caveats
- API returns text/plain, not JSON; parse accordingly
- Rate limit: 3 requests/sec recommended; add small delays in loops
- KEGG requires attribution for commercial use

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
