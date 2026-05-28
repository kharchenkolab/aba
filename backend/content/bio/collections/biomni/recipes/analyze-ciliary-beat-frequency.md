---
name: analyze-ciliary-beat-frequency
description: Measure ciliary beat frequency from high-speed video microscopy using FFT on intensity time series
when_to_use: When a user has a high-speed video of beating cilia and needs beat frequency quantification per region of interest
requires_tools: [run_python]
capabilities_needed: [opencv-python, numpy, scipy, pandas]
keywords: [cilia, beat frequency, CBF, FFT, high-speed video, motility, airway, primary ciliary dyskinesia]
produces: [per-ROI dominant frequency in Hz, median CBF, CSV results file, research log]
domain: physiology
source: biomni:tool/physiology.py::analyze_ciliary_beat_frequency
---
# Analyze Ciliary Beat Frequency

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Open the video with `cv2.VideoCapture`; read FPS, frame count, and dimensions.
2. Lay out `roi_count` rectangular ROIs in a grid pattern (size = min(W,H)/10).
3. For each frame, convert to grayscale and record the mean pixel intensity for each ROI → intensity time series.
4. Per ROI: subtract DC component (mean), apply a Hanning window, compute FFT with `scipy.fftpack.fft`.
5. Restrict the frequency axis to `[min_freq, max_freq]` Hz and find the dominant (argmax) frequency.
6. Report per-ROI frequencies and the median CBF across all ROIs; save to CSV.

## Key decisions
- Hanning window reduces spectral leakage for short, non-stationary recordings.
- Grid-based ROI placement is automatic; users can adjust `roi_count` to increase spatial sampling.
- Median (not mean) CBF is reported to be robust to outlier ROIs.

## Caveats
- Requires high-speed video (ideally ≥100 fps for typical ciliary frequencies of 8–15 Hz).
- Mean-intensity ROI approach detects ensemble CBF, not single-cilium tracking.
- `max_freq` defaults to 30 Hz; increase for insect or fish cilia.

## In ABA
Implement with `run_python`; `ensure_capability(["opencv-python", "numpy", "scipy", "pandas"])`. Original impl: `source` -> lift to lakeFS later.
