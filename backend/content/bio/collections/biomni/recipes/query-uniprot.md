---
name: query-uniprot
description: Query the UniProt REST API for protein records, sequences, and annotations
when_to_use: When you need protein function, sequence, taxonomy, or annotation data from UniProt/Swiss-Prot
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [uniprot, protein, swissprot, trembl, accession, gene, annotation, sequence]
produces: [protein records, sequence data, functional annotations]
domain: database
source: "biomni:tool/database.py::query_uniprot"
---
# Query UniProt

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://rest.uniprot.org`
2. For known accession: GET `https://rest.uniprot.org/uniprotkb/{accession}` (returns JSON)
3. For search: GET `https://rest.uniprot.org/uniprotkb/search` with params:
   - `query`: field-scoped e.g. `gene_exact:INS AND organism_id:9606`
   - `format`: `json`
   - `size`: max results
4. Prefer reviewed (Swiss-Prot) entries; scope with `reviewed:true` in query
5. Common field prefixes: `accession:`, `gene:`, `gene_exact:`, `organism_id:`, `protein_name:`
6. Use quotes for multi-word terms: `organism_name:"Homo sapiens"`
7. Parse response JSON; top-level key is `results` (list of entry objects)

## Key decisions
- Default to human (organism_id:9606) unless otherwise specified
- Use `gene_exact:` for precise gene name lookups to avoid false matches
- Search reviewed entries first; fall back to unreviewed if needed

## Caveats
- Rate limit: be respectful; add delays for bulk queries
- Some endpoints return paginated results via `Link` header `rel="next"`
- UniProt IDs (e.g. P01308) vs entry names (e.g. INS_HUMAN) are both valid accessions

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
