---
name: analyze-cell-senescence-and-apoptosis
description: Parse a flow cytometry FCS file to quantify senescent (SA-beta-Gal+) and apoptotic (Annexin V / 7-AAD) cell populations.
when_to_use: When given an FCS file from a senescence/apoptosis panel and asked to report population percentages or gating results.
requires_tools: [run_python]
capabilities_needed: [FlowCytometryTools, numpy]
keywords: [flow cytometry, FCS, senescence, SA-beta-galactosidase, apoptosis, Annexin V, 7-AAD, early apoptosis, late apoptosis, cell death]
produces: [population percentage summary, CSV results file, research log string]
domain: cancer_biology
source: biomni:tool/cancer_biology.py::analyze_cell_senescence_and_apoptosis
---
# Analyze Cell Senescence and Apoptosis

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the FCS file with `FlowCytometryTools.FCMeasurement`.
2. Remove debris by gating FSC-A > 10000 and SSC-A > 5000 (fall back to first two channels if standard names absent).
3. Identify the SA-beta-Gal channel by scanning channel names for "GAL" or "FITC"; fall back to first fluorescence channel.
4. Threshold SA-beta-Gal at the 80th percentile of the filtered population; count cells above as senescent.
5. Identify Annexin V channel (PE or "ANNEXIN") and 7-AAD channel (PerCP or "7AAD"/"AAD").
6. Threshold both at their respective 90th percentiles.
7. Gate early apoptotic (Annexin V+ / 7-AAD-) and late apoptotic/necrotic (Annexin V+ / 7-AAD+) populations.
8. Write percentages to a CSV named after the FCS file; return a structured research log.

## Key decisions
- Percentile-based thresholds are a no-controls fallback; real experiments should use unstained or isotype controls.
- Channel auto-detection by name substring; document the actual panel channel names if known.
- Senescence gate uses 80th percentile; apoptosis gates use 90th percentile to be more stringent.

## Caveats
- FlowCytometryTools uses numeric expression-string gating; channel names with special characters may need quoting.
- SA-beta-Gal is conventionally measured by microscopy/plate reader; FITC proxy is an approximation.
- Overlapping populations (senescent + apoptotic) are not cross-referenced.

## In ABA
Implement with `run_python`; `ensure_capability("FlowCytometryTools", "numpy")`. Original impl: `biomni:tool/cancer_biology.py::analyze_cell_senescence_and_apoptosis` -> lift to lakeFS later.
