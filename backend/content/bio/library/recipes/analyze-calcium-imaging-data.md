---
name: analyze-calcium-imaging-data
description: Extract neuronal activity metrics from GCaMP fluorescence image stacks via segmentation, dF/F event detection, and decay-time fitting
when_to_use: When processing two-photon or widefield calcium imaging recordings to quantify per-neuron event rates, SNR, and decay kinetics
requires_tools: [run_python]
capabilities_needed: [scikit-image, scipy, numpy, pandas]
keywords: [calcium imaging, GCaMP, neuronal activity, dF/F, event detection, decay time, SNR, segmentation, watershed, fluorescence]
produces: [neuron_activity_metrics.csv, neuron_time_series.csv]
domain: bioengineering
source: biomni:tool/bioengineering.py::analyze_calcium_imaging_data
---
# Analyze Calcium Imaging Data

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the TIFF image stack (T × H × W) with `skimage.io.imread`.
2. Compute a mean-projection image; apply Gaussian smoothing (sigma=2) with `skimage.filters.gaussian`.
3. Segment neurons via watershed: run `ndimage.distance_transform_edt` on the smoothed mean, find local maxima (`skimage.feature.peak_local_max`, min_distance=10), label them, and run `skimage.segmentation.watershed` with an Otsu mask. Fall back to simple Otsu thresholding if no maxima are found.
4. For each segmented region, extract the per-frame mean intensity across the ROI mask → per-neuron time series array.
5. For each time series:
   - Normalize: subtract 20th-percentile baseline, divide by baseline (dF/F proxy).
   - Threshold-crossing event detection: threshold = 2 × std of normalized trace.
   - Event rate (events/min) assuming 10 Hz acquisition.
   - Fit exponential decay `a·exp(−t/τ) + c` to the post-peak window of each event using `scipy.optimize.curve_fit`; average τ converted to seconds.
   - SNR = mean peak amplitude at events / std of non-event frames.
6. Compile per-cell DataFrame (Cell_ID, Event_Rate, Decay_Time, SNR); save to CSV.
7. Save full time-series matrix (columns = cells) to CSV.

## Key decisions
- Acquisition rate is hard-coded to 10 Hz; expose as a parameter for other systems.
- Decay window is 30 frames post-event; adjust for slower indicators (RCaMP, jRGECO).
- SNR uses frames > 5 steps away from any event as "noise" region.

## Caveats
- Watershed segmentation assumes spatially distinct round somata; dendrite-heavy preparations will over-segment.
- Curve fitting for decay will silently skip events where `curve_fit` fails to converge.
- 20th-percentile baseline can be biased in highly active neurons; consider using a rolling minimum instead.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "scipy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
