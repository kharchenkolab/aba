---
name: find-alternative-drugs-ddinter
description: Find alternative drugs in the same therapeutic class that avoid major interactions with a list of contraindicated drugs
when_to_use: When a drug cannot be used due to interactions and a safer substitute from the same class is needed
requires_tools: [run_python]
capabilities_needed: [pandas, pickle]
keywords: [drug substitution, alternative drug, DDInter, drug interaction avoidance, therapeutic class, polypharmacy]
produces: [ranked alternative drug list with interaction counts and risk labels, primary recommendation with rationale]
domain: pharmacology
source: biomni:tool/pharmacology.py::find_alternative_drugs_ddinter
---
# Find Alternative Drugs (DDInter)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load DDInter pickle data; standardize target drug and all contraindicated drug names via fuzzy matching.
2. Retrieve target drug's therapeutic categories from the registry.
3. Iterate all drugs in the registry; apply optional `therapeutic_class` filter (substring match on category strings) or fall back to same-category-as-target filter.
4. For each candidate, check interactions with each contraindicated drug in the interaction matrix; exclude candidates that have any Major-level interaction with any contraindicated drug.
5. Record total interaction count with contraindicated drugs for remaining candidates.
6. Sort by interaction count ascending (fewest interactions = safest); return top 10 with risk labels: No known interactions / Low / Moderate / Higher.
7. Recommend the top candidate with rationale; note if no alternatives are found.

## Key decisions
- Only Major interactions trigger exclusion; Moderate and Minor interactions are counted but do not disqualify.
- Therapeutic equivalence is not verified; the agent only checks interaction safety.

## Caveats
- DDInter coverage is limited; absence of an interaction record does not guarantee safety.
- Therapeutic equivalence and dosing must be verified clinically before substitution.
- Off-target effects outside the DDInter schema are not captured.

## In ABA
Implement with `run_python`; `ensure_capability("pandas")`. Requires DDInter pickle data. Original impl: `source` -> lift to lakeFS later.
