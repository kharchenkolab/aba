---
name: analyze-atp-luminescence-assay
description: Quantify intracellular ATP concentration from luminescence plate-reader data using a standard curve
when_to_use: Given CSV luminescence readings, a standard curve CSV, and optional normalization data to determine ATP content per cell or per mg protein
requires_tools: [run_python]
capabilities_needed: [numpy, pandas]
keywords: [ATP, luminescence, bioluminescence, standard curve, normalization, cell viability, metabolic activity]
produces: [CSV with ATP concentrations (nM) and normalized values per cell or per mg protein, summary statistics]
domain: pathology
source: biomni:tool/pathology.py::analyze_atp_luminescence_assay
---
# Analyze ATP Luminescence Assay

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load sample CSV (columns: `Sample_ID`, `Luminescence_Value`) with `pandas.read_csv`.
2. Load standard curve CSV (columns: `ATP_Concentration`, `Luminescence_Value`).
3. Fit a linear model (degree-1 `numpy.polyfit`) of Luminescence → ATP_Concentration to get slope and intercept.
4. Apply the equation `ATP_nM = slope × Luminescence + intercept` to all samples.
5. If normalization data is provided (CSV or dict):
   - `cell_count`: compute `ATP_pmol_per_million_cells = ATP_nM / count × 1000`.
   - `protein_content`: compute `ATP_nmol_per_mg_protein = ATP_nM / protein_conc`.
6. Save enriched DataFrame to `atp_measurement_results.csv`.
7. Report mean/median/min/max for both raw and normalized columns.

## Key decisions
- Linear regression assumes the standard curve is linear across the measurement range; verify R² before accepting results.
- Normalization is per-sample via a join on `Sample_ID`; samples missing from the norm table are silently skipped.
- No blank subtraction step — assumes luminescence values are already blank-corrected or that background is captured in the standard curve intercept.

## Caveats
- Negative ATP concentrations can appear if luminescence is below the standard curve baseline; clamp or flag these.
- Standard curve must be acquired under identical reagent conditions; date-matching with sample batch is important.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
