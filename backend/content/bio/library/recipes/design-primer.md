---
name: design-primer
description: Design a single PCR or sequencing primer within a sliding window using GC% and Tm filters
when_to_use: When a single primer is needed at a given position in a DNA sequence with defined GC and Tm constraints
requires_tools: [run_python]
capabilities_needed: ["biopython"]
keywords: [primer design, PCR, sequencing, GC content, melting temperature, Tm, Wallace rule]
produces: [best primer sequence with position, GC fraction, Tm, and score]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::design_primer
---
# Design Primer

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Extract a candidate window: `sequence[start_pos : start_pos + search_window]`; return `None` if shorter than `primer_length`.
2. Slide a window of `primer_length` across the candidate region, generating all candidate sequences.
3. For each candidate:
   - Compute GC fraction: `(G + C) / primer_length`; skip if outside `[min_gc, max_gc]`.
   - Compute Tm with `Bio.SeqUtils.MeltingTemp.Tm_Wallace(candidate)`; skip if outside `[min_tm, max_tm]`.
   - Score: `|gc - ideal_gc| × 100 + |tm - ideal_tm|` where ideals are midpoints of the allowed ranges.
4. Return the candidate with the lowest score as a dict: `sequence`, `position` (absolute), `gc`, `tm`, `score`. Return `None` if no candidate passes.

## Key decisions
- Wallace rule (`2(A+T) + 4(G+C)`) is used for Tm; appropriate for short oligos (~20 bp).
- Scoring weights GC penalty by ×100 to make GC deviations comparable to Tm deviations in °C.
- Returns `None` rather than raising an exception when no primer passes filters.

## Caveats
- Does not check for hairpins, self-complementarity, or primer dimers.
- `position` in the return dict is relative to the full input `sequence`, not the window.
- This is a building block used by `design_verification_primers`; prefer that for full-region coverage.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Import `from Bio.SeqUtils import MeltingTemp as mt`. Original impl: `source` -> lift to lakeFS later.
