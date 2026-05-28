---
name: analyze-interaction-mechanisms
description: Deep-dive mechanism analysis for a specific drug pair using DDInter categories and severity
when_to_use: After identifying an interaction with query-drug-interactions, to understand the mechanism and get management guidance for one specific pair
requires_tools: [run_python]
capabilities_needed: [pandas, pickle]
keywords: [drug interaction, mechanism, pharmacokinetics, pharmacodynamics, DDInter, CYP, drug safety]
produces: [drug profiles with interaction counts, per-interaction severity and category-level mechanism, summary recommendation]
domain: pharmacology
source: biomni:tool/pharmacology.py::analyze_interaction_mechanisms
---
# Analyze Interaction Mechanisms

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load DDInter pickle data; fuzzy-standardize both drug names with cutoff 0.8.
2. Retrieve drug profiles from the registry: therapeutic categories, total interaction count.
3. Look up the specific pair in the bidirectional interaction matrix.
4. For each interaction record, report severity (Major/Moderate/Minor) and DDInter category.
5. If `detailed_analysis=True`, add severity-specific clinical impact statements and category-specific mechanism descriptions (e.g., alimentary_tract → GI absorption or metabolic interactions; antineoplastic → bone marrow suppression; blood_organs → coagulation pathway; etc.).
6. Summarize severity distribution and provide an overall recommendation: AVOID / MONITOR / AWARENESS.
7. Append mechanistic considerations: additive effects, altered metabolism, timing of administration.

## Key decisions
- Mechanism text is category-level heuristic (8 DDInter categories mapped to short phrases), not curated molecular detail.
- A single drug pair may have multiple interaction records if it appears in several DDInter category files.

## Caveats
- Detailed mechanistic information (CYP isoforms, transporter involvement) is not available in DDInter 2.0; richer detail requires supplementary databases (DrugBank, CPIC).
- Only drugs present in DDInter can be analyzed; off-label or novel agents will return "not found."

## In ABA
Implement with `run_python`; `ensure_capability("pandas")`. Requires DDInter pickle data. Original impl: `source` -> lift to lakeFS later.
