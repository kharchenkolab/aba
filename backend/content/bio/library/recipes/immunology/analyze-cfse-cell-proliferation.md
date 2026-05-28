---
name: analyze-cfse-cell-proliferation
description: Quantify cell division index and percent proliferating cells from CFSE flow cytometry FCS files
when_to_use: When given an FCS file from a CFSE dilution assay and needing proliferation metrics (division index, percent proliferating, generation distribution)
requires_tools: [run_python]
capabilities_needed: [FlowCytometryTools, numpy]
keywords: [CFSE, cell proliferation, division index, flow cytometry, FCS, lymphocyte, gating, cell division]
produces: [division index, percent proliferating, generation counts, analysis log]
domain: immunology
source: biomni:tool/immunology.py::analyze_cfse_cell_proliferation
---
# Analyze CFSE Cell Proliferation

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load FCS file with `FlowCytometryTools.FCMeasurement`.
2. Apply lymphocyte gate on FSC-A / SSC-A: use provided manual bounds or auto-gate at ±80% of median FSC/SSC.
3. Extract CFSE channel data (default `FL1-A`); log10-transform (+1 offset to avoid log(0)).
4. Build 100-bin histogram of log-CFSE; find local maxima as generation peaks.
5. Sort peaks descending by intensity (highest = undivided generation 0).
6. If single peak: estimate proliferation via threshold at log-peak minus 0.3 log units; assume 1 mean division.
7. If multiple peaks: define generation boundaries at midpoints between consecutive peaks; count cells per generation.
8. Division index = `sum(i * count_i) / total_cells`.
9. Percent proliferating = `sum(count_i for i > 0) / total_cells * 100`.
10. Fallback to synthetic 4-generation data (1000/800/600/400 cells) when FlowCytometryTools is unavailable.

## Key decisions
- `collections.MutableMapping` monkey-patch applied before import for Python 3.10+ compatibility.
- Log-transform before peak detection improves separation of closely spaced generations.
- Graceful degradation to mock data ensures pipeline does not break in test environments.

## Caveats
- Automatic FSC/SSC gating assumes a single dominant lymphocyte population; debris or doublets can distort results.
- Peak detection on a 100-bin histogram may miss closely spaced late generations; consider Gaussian mixture modeling for higher resolution.
- Division index underestimates if cells die after dividing (dead cells not counted in lower generations).

## In ABA
Implement with `run_python`; `ensure_capability("FlowCytometryTools", "numpy")`. Original impl: `source` -> lift to lakeFS later.
