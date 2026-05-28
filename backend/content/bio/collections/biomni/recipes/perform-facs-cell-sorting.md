---
name: perform-facs-cell-sorting
description: Gate an FCS or CSV flow-cytometry dataset on a single fluorescence channel using min/max thresholds and report enrichment statistics.
when_to_use: When given a flow-cytometry data file (FCS or CSV) and the goal is to in-silico sort/gate a cell population based on a fluorescence parameter.
requires_tools: [run_python]
capabilities_needed: [flowkit, pandas]
keywords: [FACS, cell sorting, flow cytometry, gating, fluorescence, FCS file, enrichment, GFP, FITC, PE]
produces: [sorted_cells.csv, research log with enrichment statistics]
domain: cell_biology
source: biomni:tool/cell_biology.py::perform_facs_cell_sorting
---
# Perform FACS Cell Sorting

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load data: if the path ends in `.fcs`, use `flowkit.Sample` → `get_dataframe()`; otherwise `pd.read_csv`. Accept a DataFrame directly as well.
2. Validate that the requested `fluorescence_parameter` column exists; raise a clear error if not.
3. Apply gates: filter rows where the parameter is `>= threshold_min` (if set) and `<= threshold_max` (if set); log cell count after each gate.
4. Compute enrichment: `sorted_count / original_count × 100`; report mean and median fluorescence of the sorted population.
5. Save the gated DataFrame to `output_file` (CSV).

## Key decisions
- Single-parameter rectangular gating only; for polygon or bivariate gates extend with FlowKit's `Gate` objects.
- `flowkit` is the preferred FCS reader; fallback to `pd.read_csv` when it is unavailable, which loses FCS metadata.

## Caveats
- No compensation or transformation (e.g., logicle/biexponential) is applied; raw channel values are used directly.
- Only one fluorescence parameter is gated at a time; multi-marker immunophenotyping requires `analyze_flow_cytometry_immunophenotyping`.
- FCS channel names must match exactly the string passed as `fluorescence_parameter`.

## In ABA
Implement with `run_python`; `ensure_capability("flowkit", "pandas")`. Original impl: `source` -> lift to lakeFS later.
