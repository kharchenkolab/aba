---
name: analyze-cytokine-production-in-cd4-tcells
description: Quantify IFN-gamma and IL-17 production in CD4+ T cells across antigen stimulation conditions using flow cytometry FCS files
when_to_use: When given FCS files for multiple stimulation conditions (e.g. unstimulated, Mtb300, CMV, SEB) and needing background-subtracted cytokine frequencies in CD4+ T cells
requires_tools: [run_python]
capabilities_needed: [FlowCytometryTools, pandas]
keywords: [cytokine, IFN-gamma, IL-17, CD4, T cells, intracellular staining, flow cytometry, FCS, antigen stimulation, Mtb, CMV, SEB]
produces: [cytokine_frequencies.csv, analysis log with background-subtracted frequencies]
domain: immunology
source: biomni:tool/immunology.py::analyze_cytokine_production_in_cd4_tcells
---
# Analyze Cytokine Production in CD4+ T Cells

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `fcs_files_dict` mapping condition names to FCS file paths; expected conditions: `unstimulated`, `Mtb300`, `CMV`, `SEB`.
2. For each condition: load with `FlowCytometryTools.FCMeasurement`; attempt `sample.compensate()` (skip gracefully if no matrix).
3. Detect CD4 channel by name substring `"CD4"`; detect cytokine channels by `"IFN"` or `"IL-17"`.
4. Gate CD4+ cells: threshold `CD4_channel > 1000` (adjust per data).
5. For each cytokine channel within CD4+ gate: threshold positive cells at intensity > 500; compute frequency = positive / CD4+ * 100.
6. Build a pandas DataFrame of frequencies per condition × cytokine.
7. Subtract unstimulated background from all stimulated conditions (clip to 0).
8. Save results to `cytokine_frequencies.csv`; compare responses across stimuli in the log.

## Key decisions
- Channel detection by substring allows flexibility across different FCS naming conventions.
- Background subtraction uses the `unstimulated` condition; warns if missing.
- Static threshold values (1000 for CD4, 500 for cytokines) are placeholders — should be adjusted interactively or via a gate file in real use.

## Caveats
- Hard-coded thresholds will misclassify cells if fluorescence scales differ between instruments; use data-driven gating (e.g. k-means or FlowJo workspace import) for robust results.
- Compensation matrix must be embedded in the FCS file for `compensate()` to work.
- Polyfunctional analysis (IFN-γ AND IL-17 co-producers) requires a joint gate not present here.

## In ABA
Implement with `run_python`; `ensure_capability("FlowCytometryTools", "pandas")`. Original impl: `source` -> lift to lakeFS later.
