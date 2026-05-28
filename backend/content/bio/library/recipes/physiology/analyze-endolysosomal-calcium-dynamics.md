---
name: analyze-endolysosomal-calcium-dynamics
description: Quantify Ca2+ dynamics in endo-lysosomal compartments from ELGA/ELGA1 luminescence probe data
when_to_use: When a user has luminescence time-series from ELGA/ELGA1 probes and needs baseline, peak, kinetics, and AUC metrics
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [calcium, lysosome, endosome, ELGA, Ca2+ dynamics, peak detection, AUC, organelle, luminescence]
produces: [baseline Ca2+ level, peak count and amplitude, response time, half-decay time, AUC, CV, text results file, research log]
domain: physiology
source: biomni:tool/physiology.py::analyze_endolysosomal_calcium_dynamics
---
# Analyze Endolysosomal Calcium Dynamics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Convert inputs to numpy arrays.
2. Compute baseline: mean ± std of pre-treatment timepoints (if `treatment_time` given) or first 10% of data.
3. Normalize luminescence to baseline (normalized = raw / baseline).
4. Detect Ca2+ peaks using `scipy.signal.find_peaks(normalized, height=1.1, distance=5)`.
5. Identify max peak; if `treatment_time` provided, compute response time = peak_time - treatment_time.
6. Compute AUC of (normalized - 1) over time using `numpy.trapezoid`.
7. Find half-decay time: scan post-peak values for first point below 1 + (peak-1)/2.
8. Compute coefficient of variation = std(normalized)/mean(normalized) as a fluctuation index.
9. Write all metrics to a text file; return a research log string.

## Key decisions
- `height=1.1` requires peaks at least 10% above baseline — avoids noise false positives.
- AUC of (signal - 1) represents net Ca2+ load above resting level.
- Half-decay time uses the 50% recovery point relative to the peak (not absolute).

## Caveats
- Assumes single-compartment luminescence; signal mixing from ER or cytoplasm should be controlled experimentally.
- `numpy.trapezoid` requires evenly or unevenly spaced time points — both work.
- If no peaks are detected, AUC is still computed (may be negative if signal dips below baseline).

## In ABA
Implement with `run_python`; `ensure_capability(["numpy", "scipy"])`. Original impl: `source` -> lift to lakeFS later.
