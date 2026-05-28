---
name: find-restriction-enzymes
description: Discover which commercially common restriction enzymes cut a DNA sequence and at what positions
when_to_use: Use when surveying a sequence for all commercially available restriction sites without specifying particular enzymes in advance
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [restriction enzyme, commercially available, CommOnly, cut site, cloning, MCS, site survey]
produces: [enzyme_sites_map, cut_positions]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::find_restriction_enzymes
---
# Find Restriction Enzymes

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Convert sequence to uppercase and wrap in `Bio.Seq`.
2. Call `Bio.Restriction.CommOnly.search(seq, linear=not is_circular)` — `CommOnly` is the Biopython batch of commercially supplied enzymes.
3. Filter to only enzymes with at least one hit: `{str(enzyme): list(positions) for enzyme, positions in analysis.items() if positions}`.
4. Return `{enzyme_sites: <filtered dict>}`.

## Key decisions
- `CommOnly` restricts the search to enzymes available from NEB and other major suppliers, keeping results practically actionable.
- No overhang details are returned; use `find-restriction-sites` if cut geometry is needed.
- Topology flag `linear=not is_circular` ensures sites at the origin are found for circular sequences.

## Caveats
- `CommOnly` membership may change with Biopython version updates.
- Returns only enzymes that cut; non-cutting enzymes are excluded (unlike `find-restriction-sites`).
- Positions are 1-based (Biopython convention).

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Original impl: `source` -> lift to lakeFS later.
