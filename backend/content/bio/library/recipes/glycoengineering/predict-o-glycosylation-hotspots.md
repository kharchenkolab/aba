---
name: predict-o-glycosylation-hotspots
description: Heuristic scan for O-GalNAc glycosylation hotspots using local S/T density windows, with optional proline-exclusion filter.
when_to_use: When given a protein sequence and asked to flag candidate O-glycosylation sites or S/T-rich segments as a fast baseline before running NetOGlyc.
requires_tools: [run_python]
capabilities_needed: []
keywords: [O-glycosylation, GalNAc, mucin, serine, threonine, glycosite, glycoprotein, hotspot, sequence analysis]
produces: [list of candidate positions with S/T window fractions, research log string]
domain: glycoengineering
source: biomni:tool/glycoengineering.py::predict_o_glycosylation_hotspots
---
# Predict O-Glycosylation Hotspots

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Uppercase the sequence; enforce odd window size (default 7, reset to 7 if even or < 3).
2. For each S or T residue, extract a window of `window` residues centred on it (clamped at sequence boundaries).
3. Compute S/T fraction within that window segment.
4. Optionally skip the site if the next residue is P (`disallow_proline_next=True`, default).
5. Flag sites where S/T fraction >= `min_st_fraction` (default 0.4).
6. Report up to 100 candidates with 1-based position, residue identity, fraction, and window range.
7. Append a note directing users to NetOGlyc 4.0 for authoritative prediction.

## Key decisions
- Pure Python; no third-party dependencies.
- Window fraction threshold is a heuristic proxy for mucin-like clustering, not a learned model.
- Proline exclusion reflects the well-known inhibitory effect on O-glycosylation.

## Caveats
- This is a coarse baseline; false positive rate is high for serine/threonine-rich but non-glycosylated regions.
- Does not differentiate O-GalNAc from other O-glycan types (O-Man, O-Fuc, O-Glc).
- Use NetOGlyc 4.0 (https://services.healthtech.dtu.dk/services/NetOGlyc-4.0/) for validated predictions.

## In ABA
Implement with `run_python` (pure Python, no extra libs). Original impl: `biomni:tool/glycoengineering.py::predict_o_glycosylation_hotspots` -> lift to lakeFS later.
