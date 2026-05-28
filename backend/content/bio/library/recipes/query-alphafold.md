---
name: query-alphafold
description: Query the AlphaFold Database for protein structure predictions by UniProt ID
when_to_use: When you need predicted 3D protein structures, per-residue confidence (pLDDT), or domain annotations from AlphaFold
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [alphafold, protein structure, pLDDT, pdb, cif, uniprot, structure prediction]
produces: [structure metadata, pdb/cif files, per-residue annotations]
domain: database
source: "biomni:tool/database.py::query_alphafold"
---
# Query AlphaFold

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base API URL: `https://alphafold.ebi.ac.uk/api`
2. Three endpoint modes:
   - `prediction`: GET `{base}/prediction/{uniprot_id}` — returns list of model entries with metadata
   - `summary`: GET `{base}/uniprot/summary/{uniprot_id}.json` — concise summary
   - `annotations`: GET `{base}/annotations/{uniprot_id}[/{start}-{end}]` — per-residue features
3. To download a structure file, construct the filename:
   `AF-{uniprot_id}-F{model_number}-model_{version}.{pdb|cif}`
   and fetch from `https://alphafold.ebi.ac.uk/files/{filename}`
4. Default: version `v4`, model number `1` (highest confidence), format `pdb`
5. Response JSON for `prediction` is a list; first element has keys like `pdbUrl`, `cifUrl`, `plddt`, `uniprotStart`, `uniprotEnd`

## Key decisions
- Use `v4` (latest model) unless historical comparison is needed
- Model number 1 = best confidence; models 2–5 are alternative conformations
- For large proteins AlphaFold may split into fragments (F1, F2, …)

## Caveats
- Not all UniProt entries have AlphaFold models; 404 means no prediction available
- pLDDT scores below 70 indicate low-confidence regions (often disordered loops)
- Residue range parameter uses 1-based inclusive coordinates

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
