---
name: find-restriction-sites
description: Identify recognition sites and cut positions for a specified list of restriction enzymes in a DNA sequence
when_to_use: Use when checking which of a given set of restriction enzymes cut a sequence and where, including overhang details
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [restriction site, restriction enzyme, recognition sequence, cut position, overhang, sticky end, blunt end, cloning]
produces: [restriction_sites_map, overhang_types, cut_positions]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::find_restriction_sites
---
# Find Restriction Sites

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Create `Bio.Seq` from uppercase input sequence.
2. Build a `Bio.Restriction.RestrictionBatch(enzymes)` from the list of enzyme names.
3. Call `rb.search(seq, linear=not is_circular)` to get a dict mapping enzyme objects to cut position lists.
4. For each enzyme with positions: record `recognition_sequence` from `enzyme.elucidate()`, cut geometry (`fst5`, `fst3`, `ovhg`), and classify overhang as `"sticky"` (ovhg != 0) or `"blunt"` (ovhg == 0).
5. Include enzymes with no sites as empty lists.
6. Return structured dict with `sequence_info` and `restriction_sites` keyed by enzyme name.

## Key decisions
- `RestrictionBatch` handles batch querying more efficiently than per-enzyme calls.
- `elucidate()` returns the recognition sequence with cut indicators (e.g., `G^AATTC`).
- Both cut enzymes and non-cutters are reported for completeness.

## Caveats
- Enzyme names must match Biopython Restriction module names exactly (e.g., `"EcoRI"` not `"EcoR1"`).
- Positions from `search()` are 1-based in Biopython; verify when integrating with 0-based coordinate systems.
- Circular topology affects whether sites spanning the sequence origin are detected.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Original impl: `source` -> lift to lakeFS later.
