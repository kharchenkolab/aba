---
name: analyze-intracellular-calcium-with-rhod2
description: Estimate intracellular calcium concentration from Rhod-2 fluorescence microscopy using background subtraction and Kd-based calibration
when_to_use: Given background, control, and stimulated grayscale Rhod-2 fluorescence images and a request to quantify intracellular calcium
requires_tools: [run_python]
capabilities_needed: [opencv-python, numpy, matplotlib]
keywords: [calcium, Rhod-2, fluorescence, intracellular, Ca2+, microscopy, calibration, heatmap]
produces: [calcium concentration heatmap PNG, mean intracellular Ca2+ in nM]
domain: pathology
source: biomni:tool/pathology.py::analyze_intracellular_calcium_with_rhod2
---
# Analyze Intracellular Calcium with Rhod-2

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load all three images as float grayscale with `cv2.imread(..., IMREAD_GRAYSCALE).astype(float)`.
2. Background subtraction: `corrected = max(image - background, 0)` (clip negatives to zero).
3. Compute mean fluorescence intensities: `F_control` and `F_sample` from corrected images.
4. Apply Rhod-2 calibration: Kd = 570 nM; set F_min = F_control; F_max = 2.5 × F_sample (estimated saturation).
5. Mean [Ca²⁺] = Kd × (F − F_min) / (F_max − F); guard against F_max == F.
6. Per-pixel calcium map: `(sample_corrected − control_corrected) / (F_max − control_corrected + 1e-10) × Kd`.
7. Plot heatmap with `matplotlib` (colormap "hot"), colorbar labeled in nM; save to output_dir.

## Key decisions
- F_max = 2.5 × sample intensity is an estimate; a proper calibration requires ionomycin/EGTA saturation and zero-calcium images.
- Kd = 570 nM is the standard Rhod-2 Kd at 22 °C; adjust for temperature or cellular environment as needed.
- Per-pixel map uses a simplified pixel-wise formula rather than the strict ratio imaging formula.

## Caveats
- Without true F_max and F_min calibration images the absolute [Ca²⁺] values are semi-quantitative.
- Cells must be clearly distinguishable from background; no cell mask is applied — all pixels contribute to the mean.
- Image registration between background/control/sample frames is not performed; co-registration may be needed for moving cells.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "numpy", "matplotlib")`. Original impl: `source` -> lift to lakeFS later.
