---
name: enumerate-bacterial-cfu-by-serial-dilution
description: Simulate serial dilution spot-plating to enumerate bacterial CFU/mL and identify countable dilution ranges
when_to_use: When asked to design or analyze a serial dilution CFU assay given estimated concentration, dilution factor, and number of dilutions
requires_tools: [run_python]
capabilities_needed: [numpy, pandas]
keywords: [CFU enumeration, serial dilution, spot plating, colony forming units, bacterial quantification, microbial counting]
produces: [per-dilution spot counts CSV, final CFU/mL estimate, research log]
domain: microbiology
source: biomni:tool/microbiology.py::enumerate_bacterial_cfu_by_serial_dilution
---
# Enumerate Bacterial CFU by Serial Dilution

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Compute theoretical concentrations at each dilution step: `C_i = C0 / dilution_factor^i`.
2. For each dilution, simulate `spots_per_dilution` colony counts using Poisson sampling (`np.random.poisson`) on expected count per 10 µL spot; mark TMTC (too-many-to-count) when expected > 300.
3. Collect all results into a pandas DataFrame with columns: Dilution, Dilution_Factor, Spot, CFU_Count.
4. Identify countable dilutions: those where average spot count is between 3 and 300 (excluding TMTC strings).
5. Back-calculate concentration from each countable dilution: `CFU/mL = avg_count × 100 × dilution_factor^i`.
6. Average across countable dilutions for the final estimate.
7. Save full results to CSV; return a research log with dilution table, countable dilutions, and final CFU/mL.

## Key decisions
- Spot volume is 10 µL (0.01 mL); multiply by 100 to convert to per-mL.
- Countable range 3–300 CFU/spot follows standard microbiological practice.
- Simulation uses fixed random seed 42 for reproducibility.

## Caveats
- This is a Monte Carlo simulation for experimental planning; replace simulated counts with actual plate counts for real quantification.
- If no countable dilutions are found, advise adjusting the dilution series.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
