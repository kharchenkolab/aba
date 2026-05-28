---
name: query-drug-interactions
description: Query pairwise drug-drug interactions from the local DDInter 2.0 database for a list of drugs
when_to_use: To screen a set of drugs for known interactions, optionally filtered by severity level or interaction category
requires_tools: [run_python]
capabilities_needed: [pandas, pickle]
keywords: [drug-drug interaction, DDInter, pharmacology, polypharmacy, drug safety, interaction severity]
produces: [interaction pairs with severity and category, summary statistics on severity distribution]
domain: pharmacology
source: biomni:tool/pharmacology.py::query_drug_interactions
---
# Query Drug Interactions

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load DDInter data from pre-processed pickle files: drug info registry, bidirectional interaction matrix (keyed by standardized name), and name mapping dict.
2. Standardize each input drug name: lowercase, strip salt suffixes (hydrochloride, sulfate, sodium, etc.), then fuzzy-match against the mapping dict using `difflib.get_close_matches(cutoff=0.8)`.
3. Enumerate all unique drug pairs; look up each pair in the interaction matrix.
4. Apply optional filters: `severity_levels` (Major/Moderate/Minor) and `interaction_types` (DDInter category strings).
5. Format each hit with severity, category, and clinical significance note.
6. Report summary: total pairs analyzed, pairs with interactions, total interactions, severity distribution.

## Key decisions
- DDInter CSV files are pre-processed into standardized pickle files on first use (lazy loading).
- Fuzzy matching cutoff 0.8 balances recall vs. false positives; salt-form stripping improves name normalization.
- Interaction matrix is bidirectional so both A→B and B→A lookups work.

## Caveats
- DDInter 2.0 has limited coverage; drugs not in the database are flagged as missing rather than assumed safe.
- Mechanism descriptions are category-level heuristics, not curated molecular mechanisms.

## In ABA
Implement with `run_python`; `ensure_capability("pandas")`. DDInter pickle data must be present in the schema_db path. Original impl: `source` -> lift to lakeFS later.
