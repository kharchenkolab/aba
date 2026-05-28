---
name: analyze-flow-cytometry-immunophenotyping
description: Load an FCS file, optionally compensate, apply a multi-marker sequential gating strategy, and report counts and percentages for each defined cell population.
when_to_use: When given an FCS file and a dictionary of marker-based gates and the goal is to identify and quantify immunophenotypic cell populations (e.g., HSCs, T cells, B cells).
requires_tools: [run_python]
capabilities_needed: [FlowCytometryTools, pandas]
keywords: [immunophenotyping, flow cytometry, FCS, gating, surface markers, compensation, cell populations, PBMC, HSC, T cell, B cell]
produces: [population_summary.csv, research log with per-population event counts and percentages]
domain: cell_biology
source: biomni:tool/cell_biology.py::analyze_flow_cytometry_immunophenotyping
---
# Analyze Flow Cytometry Immunophenotyping

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the FCS file with `FlowCytometryTools.FCMeasurement(ID="Sample", datafile=fcs_file_path)`.
2. If a `compensation_matrix` (numpy array) is provided, apply `sample.compensate(matrix)`.
3. For each population in `gating_strategy` (a dict mapping name → list of `(marker, operator, threshold)` tuples):
   a. Start with a copy of the full sample.
   b. Apply gates sequentially using `population.gate(f"{marker} {op} {threshold}")`.
   c. Operator `"between"` takes a `(lower, upper)` tuple.
   d. Log cell count before and after each gate step.
4. Compute percentage of total events for each final population.
5. Assemble a summary DataFrame `[Population, Count, Percentage]`; save to `population_summary.csv`.

## Key decisions
- Gates are applied sequentially (AND logic) within each population; populations are independent of each other.
- `FlowCytometryTools` string-based `.gate()` interface is used; more complex polygon gates would require the library's `ThresholdGate` / `PolyGate` objects.

## Caveats
- Compensation is optional but important when multiple fluorochromes have spectral overlap.
- Channel names in `gating_strategy` must exactly match those in the FCS file.
- No transformation (log, logicle) is applied; for low-signal channels this may compress separation.

## In ABA
Implement with `run_python`; `ensure_capability("FlowCytometryTools", "pandas")`. Original impl: `source` -> lift to lakeFS later.
