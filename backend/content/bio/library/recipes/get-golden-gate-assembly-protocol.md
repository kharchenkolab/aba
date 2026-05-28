---
name: get-golden-gate-assembly-protocol
description: Generate a customized Golden Gate assembly protocol with calculated molar amounts and thermocycler program
when_to_use: When planning a Golden Gate cloning experiment and needing a ready-to-follow bench protocol with reagent amounts
requires_tools: [run_python]
capabilities_needed: []
keywords: [Golden Gate, cloning, Type IIS, BsaI, BsmBI, BbsI, assembly, restriction ligation, vector, insert]
produces: [protocol dict with reaction components, volumes, molar amounts, and thermocycler steps]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::get_golden_gate_assembly_protocol
---
# Get Golden Gate Assembly Protocol

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate enzyme against supported list: BsaI, BsmBI, BbsI, Esp3I, BtgZI, SapI.
2. Select thermocycler program by number of inserts:
   - 1 insert: single 37 °C step (5 min standard, 1 h library prep) + 60 °C 5 min inactivation.
   - 2–10 inserts: 30 cycles of 37 °C/1 min → 16 °C/1 min + 60 °C inactivation.
   - 11+ inserts: 30 cycles of 37 °C/5 min → 16 °C/5 min + 60 °C inactivation; use 2 µl assembly mix.
3. Calculate vector pmol: `pmol = ng / (bp × 650) × 1e6`.
4. For each insert (if lengths provided): target 2:1 insert:vector molar ratio; back-calculate insert ng.
5. Build component list: vector, inserts, T4 ligase buffer (2 µl), NEB Golden Gate mix (1 or 2 µl), H₂O to 20 µl.
6. Return protocol dict with title, description, steps, and notes.

## Key decisions
- Assembly mix volume doubles to 2 µl for 11+ insert assemblies.
- When insert lengths are not provided, include a generic placeholder with molar ratio guidance.
- 650 Da/bp is the standard dsDNA molecular weight approximation.

## Caveats
- Protocol amounts are for a 20 µl reaction; scale proportionally for larger volumes.
- Does not verify that restriction sites exist in the provided sequences; use `design_golden_gate_oligos` first.
- Library-prep mode only affects the 1-insert thermal step.

## In ABA
Implement with `run_python` (pure arithmetic, no third-party libs needed). Original impl: `source` -> lift to lakeFS later.
