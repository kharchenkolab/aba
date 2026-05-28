---
name: analyze-hemodynamic-data
description: Extract SBP, DBP, MAP, and heart rate from raw blood pressure waveform data
when_to_use: When a user has continuous blood pressure recordings in mmHg and needs standard hemodynamic parameters
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, pandas]
keywords: [blood pressure, hemodynamics, SBP, DBP, MAP, heart rate, cardiovascular, signal processing]
produces: [systolic BP, diastolic BP, mean arterial pressure, heart rate in bpm, CSV results file, research log]
domain: physiology
source: biomni:tool/physiology.py::analyze_hemodynamic_data
---
# Analyze Hemodynamic Data

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Apply a 2nd-order Butterworth bandpass filter (0.5–10 Hz) via `scipy.signal.butter` + `filtfilt` to remove baseline drift and high-frequency noise.
2. Detect systolic peaks with `scipy.signal.find_peaks(filtered, distance=sampling_rate*0.5)` — enforces a minimum 0.5 s inter-beat interval.
3. Find diastolic troughs: for each consecutive peak pair, locate the minimum of the between-peak segment.
4. Compute SBP = mean of peak values, DBP = mean of trough values.
5. MAP = DBP + (SBP - DBP) / 3.
6. Heart rate = 60 / mean(inter-peak intervals in seconds).
7. Save SBP, DBP, MAP, HR with units to CSV; return research log.

## Key decisions
- `filtfilt` (zero-phase) preserves waveform morphology without introducing phase delay.
- 0.5 s minimum peak distance corresponds to a physiological maximum of 120 bpm.
- Standard MAP formula (1/3 pulse pressure rule) is appropriate for arterial waveforms.

## Caveats
- Designed for arterial pressure waveforms; apply with caution to venous or pulmonary pressure signals.
- Ectopic beats or arrhythmias can distort mean SBP/DBP — consider adding an outlier filter on peak-to-peak intervals.
- `high_cutoff` of 10 Hz is suitable up to ~600 bpm; for rodent data (400–700 bpm) raise to 20 Hz.

## In ABA
Implement with `run_python`; `ensure_capability(["numpy", "scipy", "pandas"])`. Original impl: `source` -> lift to lakeFS later.
