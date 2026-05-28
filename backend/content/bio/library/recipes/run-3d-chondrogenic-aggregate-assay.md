---
name: run-3d-chondrogenic-aggregate-assay
description: Generate a detailed step-by-step protocol for a 3D chondrogenic aggregate culture assay to evaluate compound effects on chondrogenesis
when_to_use: When designing or documenting a 3D pellet-culture chondrogenesis experiment with test compounds
requires_tools: [run_python]
capabilities_needed: [datetime]
keywords: [chondrogenesis, cartilage, 3D culture, aggregate assay, TGF-beta, COL2A1, chondrocyte, protocol]
produces: [detailed markdown protocol document for the assay]
domain: pharmacology
source: biomni:tool/pharmacology.py::run_3d_chondrogenic_aggregate_assay
---
# Run 3D Chondrogenic Aggregate Assay Protocol

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Collect inputs: cell source, passage number, cell density, list of test compounds (name, concentration, vehicle), culture duration, and measurement interval.
2. Generate measurement time points at the specified interval (appending final day if not already included).
3. Build a structured markdown protocol covering:
   - Materials list (chondrogenic medium components, plates, reporter assay kit, centrifuge).
   - Experimental metadata (cell info, time points, compound table).
   - Day 0 setup: prepare differentiation medium (DMEM + TGF-β3 10 ng/mL + Dex 100 nM + ascorbate + ITS+ + pyruvate + P/S), harvest cells, form pellets by centrifugation at 500g in 96-well V-bottom plates, add compounds.
   - Maintenance: 50% medium change every 2–3 days; sample collection at each time point.
4. Return the full protocol string.

## Key decisions
- Protocol is purely text generation; no simulation — used for experiment planning and documentation.
- COL2A1-Gaussia luciferase reporter readout is described as the primary chondrogenic differentiation marker.
- An experiment ID (`CHOND3D_<timestamp>`) is generated for lab notebook traceability.

## Caveats
- Protocol is generic; adjust TGF-β3 concentration, cell density, and plate format to cell type and institutional SOP.
- Histological endpoint (paraformaldehyde fixation) is noted but not quantified computationally.

## In ABA
Implement with `run_python`; no special capabilities beyond stdlib datetime. Original impl: `source` -> lift to lakeFS later.
