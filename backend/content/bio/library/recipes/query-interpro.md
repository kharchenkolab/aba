---
name: query-interpro
description: Query the InterPro REST API for protein domain, family, and motif annotations
when_to_use: When you need protein domain classifications, family assignments, or cross-database signatures (Pfam, CDD, etc.)
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [interpro, protein domain, pfam, cdd, family, motif, signature, accession]
produces: [domain annotations, entry metadata, protein-to-domain mappings]
domain: database
source: "biomni:tool/database.py::query_interpro"
---
# Query InterPro

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://www.ebi.ac.uk/interpro/api`
2. Hierarchical path structure: `/{data_type}/{source}/{accession}`
   - data types: `entry`, `protein`, `structure`, `set`, `taxonomy`, `proteome`
   - sources: `interpro`, `pfam`, `cdd`, `uniprot`, `pdb`
3. Examples:
   - Specific entry: GET `{base}/entry/interpro/IPR023411`
   - Protein domains: GET `{base}/entry/interpro/protein/uniprot/{uniprot_id}`
   - All Pfam entries for a protein: GET `{base}/entry/pfam/protein/uniprot/{uniprot_id}`
4. Add pagination params: `?page=1&page_size=N`
5. Use lowercase accessions (e.g. `ipr000001` not `IPR000001`)
6. Response JSON has keys: `count`, `next`, `previous`, `results`

## Key decisions
- For protein subtypes use `reviewed` (Swiss-Prot) or `unreviewed` (TrEMBL) as source qualifier
- Cross paths to get relational data (e.g. all structures for a domain family)

## Caveats
- Paginated; follow `next` URL for additional pages
- Some path combinations are not valid; check the schema for supported combinations

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
