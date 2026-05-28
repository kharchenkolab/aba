---
name: analyze-abr-waveform-p1-metrics
description: Extract P1 amplitude and latency from an Auditory Brainstem Response waveform
when_to_use: When a user provides ABR time-series data and needs Wave I (P1) metrics for auditory function assessment
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [ABR, auditory brainstem response, P1, Wave I, peak detection, latency, amplitude, hearing]
produces: [P1 amplitude in microvolts, P1 latency in ms, research log]
domain: physiology
source: biomni:tool/physiology.py::analyze_abr_waveform_p1_metrics
---
# Analyze ABR Waveform P1 Metrics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Convert `time_ms` and `amplitude_uv` inputs to numpy arrays.
2. Detect all positive peaks using `scipy.signal.find_peaks(amplitude, height=0)`.
3. Extract peak heights and corresponding latency values.
4. Search for peaks in the canonical P1 window (1–3 ms); if none found, fall back to the first detected peak.
5. Within the P1 window, select the highest peak as P1.
6. Report P1 amplitude (µV) and P1 latency (ms).

## Key decisions
- The 1–3 ms window targets Wave I of the human ABR; adjust for animal models (rodent ABR peaks occur earlier).
- If no peaks fall in the window, the first peak is used as a conservative fallback rather than returning an error.

## Caveats
- Assumes the input waveform is already filtered/averaged; raw single-sweep data should be preprocessed first.
- `height=0` captures all positive peaks; noisy recordings may yield spurious peaks — consider adding a minimum prominence threshold.
- Does not compute N1 (the following trough) or interpeak latencies.

## In ABA
Implement with `run_python`; `ensure_capability(["numpy", "scipy"])`. Original impl: `source` -> lift to lakeFS later.
