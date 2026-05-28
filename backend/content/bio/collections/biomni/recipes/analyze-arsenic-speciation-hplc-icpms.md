---
name: analyze-arsenic-speciation-hplc-icpms
description: Identify and quantify arsenic species (As(III), As(V), MMA, DMA) in liquid samples from HPLC-ICP-MS chromatogram data
when_to_use: When given retention-time/intensity dictionaries from HPLC-ICP-MS runs and asked to speciate arsenic
requires_tools: [run_python]
capabilities_needed: [pandas, numpy]
keywords: [arsenic speciation, HPLC-ICP-MS, As(III), As(V), MMA, DMA, heavy metals, water quality, chromatography]
produces: [species concentrations CSV, predominant species report, research log]
domain: microbiology
source: biomni:tool/microbiology.py::analyze_arsenic_speciation_hplc_icpms
---
# Analyze Arsenic Speciation by HPLC-ICP-MS

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept sample data as `{sample_id: {retention_time: signal_intensity}}` and optional calibration factors per species.
2. Define reference retention times for six arsenic species: As(III) 2.8 min, As(V) 7.5 min, MMAs(III) 3.9 min, MMAs(V) 6.2 min, DMAs(III) 4.7 min, DMAs(V) 5.3 min.
3. For each sample and species, find the closest measured retention time within ±0.3 min tolerance.
4. Convert signal intensity to concentration using species-specific calibration factor; mark values below detection limit accordingly.
5. Assemble results into a pandas DataFrame; save to CSV.
6. Identify the predominant species per sample (highest numeric concentration).
7. Return a structured log covering instrument conditions, results table, and summary.

## Key decisions
- Default calibration factors (0.78–0.92) and detection limits (0.1–0.2 µg/L) are applied if none provided.
- Species not found within ±0.3 min window are recorded as "Not detected".
- Anion-exchange column with 20 mM NH4H2PO4 pH 6.0 mobile phase assumed.

## Caveats
- Calibration factors are approximate defaults; provide actual calibration standards for quantitative work.
- Method covers inorganic and methylated As species only; arsenosugars or arsenobetaine require different retention windows.

## In ABA
Implement with `run_python`; `ensure_capability("pandas")`. Original impl: `source` -> lift to lakeFS later.
