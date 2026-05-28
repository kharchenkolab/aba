---
name: golden-gate-assembly
description: Simulate Golden Gate assembly to predict the final construct sequence from a backbone and one or more fragments
when_to_use: When verifying that Golden Gate fragments will assemble correctly and to obtain the predicted assembled sequence
requires_tools: [run_python]
capabilities_needed: []
keywords: [Golden Gate, assembly simulation, Type IIS, BsaI, BsmBI, multi-fragment, cloning, construct design]
produces: [assembled sequence, assembly order, backbone and insert segment sizes]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::golden_gate_assembly
---
# Golden Gate Assembly

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Look up enzyme properties (recognition site, `offset_fwd`, `offset_rev`) for BsaI, BsmBI, BbsI, Esp3I, BtgZI, or SapI.
2. Pre-process fragments: if a fragment has `sequence` (dsDNA) instead of oligos, find forward and reverse recognition sites, compute cut positions, extract `fwd_overhang` + `insert_seq` + `rev_overhang`, then convert to equivalent `fwd_oligo`/`rev_oligo` format.
3. Scan the backbone for all recognition sites (forward and reverse strand); compute `cut_fwd`, `cut_rev`, and `overhang` for each using the same modular-arithmetic approach as `design_golden_gate_oligos`.
4. For each processed fragment, extract: `fwd_overhang = fwd_oligo[:4]`, `rev_overhang = rev_oligo[:4]`, `insert = fwd_oligo[4:]`, `rc_rev_overhang = revcomp(rev_overhang)`.
5. Build overhang→fragment index maps; identify backbone cut sites whose overhangs match a fragment's `fwd_overhang` as candidate start points.
6. Greedy chaining: starting from each candidate, follow overhang links (`rc_rev_overhang` of current → `fwd_overhang` of next) until all fragments are chained or no match found. Verify the last fragment's `rc_rev_overhang` matches a second backbone cut.
7. Assemble the final sequence: backbone segment (between `end_cut["cut_rev"]` and `start_cut["cut_fwd"]`, with circular wrap-around) + ordered insert segments (first fragment's `fwd_overhang` + each insert + each `revcomp(rev_overhang)`).
8. Return `assembled_sequence`, `assembly_order`, `backbone_fragment_size`, `fragments_used`.

## Key decisions
- Overhang length is fixed at 4 nt (standard for most Type IIS enzymes); SapI's 3-nt overhang would need adjustment.
- Greedy chain search tries all possible starting cut sites; first complete circuit wins.
- `ds_sequence` fragments require one forward site and at least one reverse site in the proper orientation; otherwise returns `success: False`.

## Caveats
- Does not simulate ligation efficiency or off-target assembly products.
- Multiple valid assembly circuits are not exhaustively found; first valid one is returned.
- Fragment order in the input list is irrelevant; correct order is inferred from overhang complementarity.
- The `print` statements in the original code are debug artifacts; omit them in production.

## In ABA
Implement with `run_python`; no third-party libs required beyond stdlib. Original impl: `source` -> lift to lakeFS later.
