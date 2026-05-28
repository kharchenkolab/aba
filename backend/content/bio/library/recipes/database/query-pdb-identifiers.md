---
name: query-pdb-identifiers
description: Fetch detailed metadata and optionally download structure files for PDB identifiers
when_to_use: After searching PDB to retrieve full entry data or download PDB/mmCIF files for a list of IDs
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [pdb, protein structure, rcsb, download, pdb file, entry details, polymer entity]
produces: [entry metadata, downloaded pdb files]
domain: database
source: "biomni:tool/database.py::query_pdb_identifiers"
---
# Query PDB Identifiers

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Data API base: `https://data.rcsb.org/rest/v1/core`
2. Endpoint by return_type:
   - `entry`: GET `{base}/entry/{pdb_id}` (e.g. `1ABC`)
   - `polymer_entity`: GET `{base}/polymer_entity/{pdb_id}/{entity_id}` (split `1ABC_1` on `_`)
   - `nonpolymer_entity`: GET `{base}/nonpolymer_entity/{pdb_id}/{entity_id}`
   - `polymer_instance`: GET `{base}/polymer_entity_instance/{pdb_id}/{asym_id}` (split `1ABC.A` on `.`)
   - `assembly`: GET `{base}/assembly/{pdb_id}/{assembly_id}` (split `1ABC-1` on `-`)
   - `mol_definition`: GET `{base}/chem_comp/{identifier}`
3. To download PDB file: GET `https://files.rcsb.org/download/{pdb_id}.pdb`
4. To filter response to specific fields, traverse the JSON path manually after fetching

## Key decisions
- Iterate identifiers one at a time; collect errors per identifier without failing the whole batch
- Extract the base PDB ID from compound identifiers before constructing the download URL

## Caveats
- Very large structures may not have a `.pdb` file; try `.cif` from `https://files.rcsb.org/download/{pdb_id}.cif`
- Data API returns rich JSON; select only needed fields to keep output manageable

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
