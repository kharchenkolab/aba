---
name: analyze-thrombus-histology
description: Classify and quantify H&E-stained thrombus components (fresh, lysis, endothelialization, fibroblastic reaction) by LAB color thresholding
when_to_use: Given an H&E histology image of a thrombus and a request to quantify tissue composition or stage of organization
requires_tools: [run_python]
capabilities_needed: [opencv-python, numpy, scikit-image]
keywords: [thrombus, histology, H&E, hematoxylin, eosin, LAB color, segmentation, pathology, coagulation]
produces: [color-coded component overlay PNG, CSV with percentage of each thrombus component]
domain: pathology
source: biomni:tool/pathology.py::analyze_thrombus_histology
---
# Analyze Thrombus Histology

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image with `cv2.imread` (BGR); convert to RGB then to LAB via `skimage.color.rgb2lab`.
2. Create boolean masks using LAB channel thresholds for four components:
   - Fresh thrombus: L > 50 and a* > 15 (bright, red-positive — erythrocytes).
   - Cellular lysis: L > 60, −5 < a* < 15, b* < 10 (pale, low-chroma degraded cells).
   - Endothelialization: 40 < L < 70 and b* < −5 (organized cell layer with blue tint).
   - Fibroblastic reaction: L > 70, −10 < a* < 5, b* > 0 (pale fibrous collagen).
3. Compute area percentages relative to sum of all four mask pixels.
4. Build visualization by assigning colors (red/green/blue/yellow) to each mask and saving with `cv2.imwrite`.
5. Write results to CSV with `csv.writer`.

## Key decisions
- LAB thresholds are approximate and tuned for standard H&E; different staining batches may need recalibration.
- Masks are non-exclusive — overlapping pixels are assigned in draw order; last-written color wins in visualization.
- Total pixels = sum of four masks (excludes background/lumen), giving relative tissue composition.

## Caveats
- No morphological cleanup on component masks; noisy staining can inflate minor-component percentages.
- Endothelialization detection relies on thin cellular-layer color which is easily confused with background.
- Thresholds should be validated against a pathologist-annotated ground truth before clinical use.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "numpy", "scikit-image")`. Original impl: `source` -> lift to lakeFS later.
