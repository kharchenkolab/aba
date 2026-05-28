---
name: analyze-fatty-acid-composition-by-gc
description: Identify and quantify fatty acids in a tissue sample from GC retention-time and peak-area data
when_to_use: When a user has a GC CSV file with retention_time and peak_area columns and needs fatty acid profiles for a tissue sample
requires_tools: [run_python]
capabilities_needed: [pandas]
keywords: [fatty acid, GC, gas chromatography, lipid, palmitic, oleic, DHA, EPA, lipidomics, tissue]
produces: [per-fatty-acid percentage composition, saturated-to-unsaturated ratio, CSV results file, research log]
domain: physiology
source: biomni:tool/physiology.py::analyze_fatty_acid_composition_by_gc
---
# Analyze Fatty Acid Composition by GC

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the CSV with `pandas`; verify `retention_time` and `peak_area` columns are present.
2. Define retention-time windows for 11 fatty acids (C14:0 through DHA C22:6, plus CLA).
3. For each window, sum `peak_area` values within the range; compute percentage = area / total_area * 100.
4. Sort results by percentage descending; save to CSV named `{sample_id}_{tissue_type}_fatty_acid_composition.csv`.
5. Classify peaks as saturated (C14:0, C16:0, C18:0) vs. unsaturated; compute the sat/unsat ratio.
6. Report top-3 most abundant fatty acids and the ratio in the research log.

## Key decisions
- Retention-time windows are hardcoded for a standard FAME (fatty acid methyl ester) GC method; adjust for instrument-specific protocols.
- Summation within a window handles peak tailing or multiple injections gracefully.
- Saturated/unsaturated classification follows conventional lipid biochemistry groupings.

## Caveats
- No internal standard normalization is applied; absolute quantification requires the user to supply a known-concentration standard.
- Retention-time windows may overlap or miss peaks if a non-standard column or temperature program is used.
- CLA (t10c12) is included but its window may conflict with EPA on some columns; verify chromatogram.

## In ABA
Implement with `run_python`; `ensure_capability(["pandas"])`. Original impl: `source` -> lift to lakeFS later.
