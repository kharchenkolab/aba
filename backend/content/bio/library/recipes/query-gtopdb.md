---
name: query-gtopdb
description: Query the Guide to PHARMACOLOGY (GtoPdb) for drug targets, ligands, and pharmacological interactions
when_to_use: When looking up pharmacological targets, ligand binding data, receptor-drug interactions, or approved drug mechanisms
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [GtoPdb, pharmacology, drug target, ligand, receptor, GPCR, interaction, agonist, antagonist, approved drug]
produces: [target records, ligand records, interaction data, pharmacological profiles]
domain: database
source: biomni:tool/database.py::query_gtopdb
---
# Query GtoPdb

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept natural language prompt or a direct endpoint URL.
2. If prompt given, use an LLM with the GtoPdb schema to produce a full endpoint URL and description.
3. Normalize endpoint: prepend `https://www.guidetopharmacology.org/services` if not a full URL.
4. GET the URL via requests; parse JSON.
5. Optionally condense when verbose is off.

## Key decisions
- Base URL: `https://www.guidetopharmacology.org/services`.
- Main endpoints: `targets`, `ligands`, `interactions`, `diseases`, `refs`.
- Target types: `GPCR`, `NHR`, `LGIC`, `VGIC`, `OtherIC`, `Enzyme`, `CatalyticReceptor`, `Transporter`, `OtherProtein`.
- Ligand types: `Synthetic organic`, `Metabolite`, `Natural product`, `Endogenous peptide`, `Antibody`, `Approved`.
- Sub-resource pattern: `targets/{targetId}/interactions`, `ligands/{ligandId}/structure`.
- Interaction types include: `Agonist`, `Antagonist`, `Allosteric modulator`, `Inhibitor`, `Channel blocker`.

## Caveats
- No authentication required.
- The API returns arrays even for single-entity queries; always handle list responses.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
