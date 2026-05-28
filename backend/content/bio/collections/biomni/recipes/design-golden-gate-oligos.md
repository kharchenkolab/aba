---
name: design-golden-gate-oligos
description: Design forward and reverse oligos for Golden Gate cloning by scanning backbone restriction sites and extracting overhangs
when_to_use: When preparing oligos to insert a sequence into a backbone via Golden Gate assembly
requires_tools: [run_python]
capabilities_needed: []
keywords: [Golden Gate, oligo design, Type IIS, overhang, BsaI, BsmBI, cloning, restriction site]
produces: [forward oligo, reverse oligo, overhang sequences, cut site positions]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::design_golden_gate_oligos
---
# Design Golden Gate Oligos

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Look up enzyme properties (recognition site, `offset_fwd`, `offset_rev`) for BsaI, BsmBI, BbsI, Esp3I, BtgZI, or SapI.
2. Clean backbone and insert sequences (upper-case, keep only ATGC).
3. Scan every position of the backbone for both the recognition site (forward) and its reverse complement (reverse strand); record all hits with strand label.
4. For each site, compute cut positions using modular arithmetic for circular backbones:
   - Forward site: `cut_fwd = pos + len(site) + offset_fwd`, `cut_rev = pos + len(site) + offset_rev`
   - Reverse site: `cut_rev = pos - offset_fwd`, `cut_fwd = pos - offset_rev`
5. Extract overhang between `cut_fwd` and `cut_rev` (with wrap-around for circular).
6. Use the first two cut sites: upstream overhang → 5' of forward oligo; downstream overhang (reverse-complemented) → 5' of reverse oligo.
   - `fw_oligo = upstream_overhang + insert_sequence`
   - `rev_oligo = revcomp(insert_sequence + revcomp(downstream_overhang))`
7. Return success dict with `overhangs`, `oligos` (fwd + rev + notes), all `cut_sites`.

## Key decisions
- Requires at least 2 recognition sites in the backbone; returns `success: False` otherwise.
- Uses first two cut sites for simplicity; more sophisticated site-selection may be needed for multi-insert assemblies.
- Reverse complement is implemented inline without Bio dependency.

## Caveats
- Overhang uniqueness is not verified; user must confirm 4-nt overhangs are not repeated elsewhere in the assembly.
- Site selection heuristic (first two) may be incorrect if the backbone has more than two sites; review cut site list manually.
- Does not add PCR flanking sequences or verify insert orientation.

## In ABA
Implement with `run_python`; no third-party libs required beyond stdlib. Original impl: `source` -> lift to lakeFS later.
