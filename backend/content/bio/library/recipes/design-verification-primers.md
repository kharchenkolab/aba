---
name: design-verification-primers
description: Design a minimal set of Sanger sequencing primers to fully cover a target region in a plasmid
when_to_use: When planning Sanger sequencing verification of a cloned insert or edited region in a plasmid
requires_tools: [run_python]
capabilities_needed: ["biopython"]
keywords: [Sanger sequencing, verification, primer design, plasmid, coverage, sequencing primers, existing primers]
produces: [recommended primer list with coverage map and full-coverage status]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::design_verification_primers
---
# Design Verification Primers

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept a plasmid sequence, target region `(start, end)`, optional existing primer pool, and coverage/filter parameters.
2. If no existing primers provided, use a built-in pool of 28 common lab primers (T7, M13F, SP6, CMV-Forward, etc.).
3. Align all existing primers against the plasmid with `align_sequences()` to find positions and strands.
4. For each alignment that overlaps the target region, compute `covered_start/end` within the target; create a `potential_primers` list sorted by coverage length descending.
5. Greedy set-cover: iteratively select the primer adding the most new coverage until the region is fully covered or primers exhausted. Merge overlapping covered segments.
6. Identify uncovered gaps; for each gap calculate how many new primers are needed (`gap_length / (coverage_length / 2)`), distribute positions evenly starting 100 bp before each gap.
7. Call `design_primer()` at each position to create new primers (GC/Tm-filtered, Wallace Tm).
8. Return `recommended_primers` list, `coverage_map`, `is_fully_covered` flag, and optional warning.

## Key decisions
- Greedy set-cover minimises total primers while maximising reuse of existing lab stocks.
- Circular plasmid wrapping is handled by extending the effective sequence beyond the origin when the target spans the origin.
- New primers are designed only where existing primers fail to cover.

## Caveats
- `align_sequences()` must be available in the ABA environment (separate recipe).
- Coverage length defaults to 800 bp (typical Sanger read), adjustable.
- New primers are all forward-strand; reverse primers from existing pool are handled via alignment strand detection.
- Does not account for secondary structure or repetitive regions that may impair reads.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Depends on `align_sequences` and `design_primer` helpers. Original impl: `source` -> lift to lakeFS later.
