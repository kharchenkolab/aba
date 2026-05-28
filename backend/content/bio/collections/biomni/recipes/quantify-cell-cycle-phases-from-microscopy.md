---
name: quantify-cell-cycle-phases-from-microscopy
description: Classify cells into G1/S/G2M phases from Calcofluor-white-stained fluorescence microscopy images using morphological segmentation and rule-based feature scoring.
when_to_use: When given fluorescence microscopy images of cells stained with Calcofluor white and the goal is to estimate the fraction of cells in each cell-cycle phase.
requires_tools: [run_python]
capabilities_needed: [scikit-image, numpy, pandas]
keywords: [cell cycle, G1, S phase, G2/M, Calcofluor white, fluorescence microscopy, cell segmentation, mitosis, yeast, septum]
produces: [cell_cycle_phases.csv, research log with per-phase counts and percentages]
domain: cell_biology
source: biomni:tool/cell_biology.py::quantify_cell_cycle_phases_from_microscopy
---
# Quantify Cell Cycle Phases from Microscopy

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load each image with `skimage.io.imread`; convert to grayscale if multichannel.
2. Denoise with `skimage.filters.gaussian(sigma=1.0)`.
3. Threshold with Otsu; clean binary mask via `morphology.remove_small_objects` (min 30 px) and `binary_closing`.
4. Label connected components with `measure.label`; extract `regionprops` (area, perimeter, eccentricity, mean_intensity).
5. Compute circularity `= 4π·area / perimeter²` per cell.
6. Detect septum proxy: for cells with area > 50 px, flag as `has_septum` when intensity std > 20% of mean AND max > 1.5× mean.
7. Rule-based phase assignment per cell:
   - G2/M: `has_septum` AND area > 1.2× median area
   - S: `has_septum` OR (area > median AND eccentricity > 0.5)
   - G1: otherwise
8. Aggregate phase counts; compute percentages; save to `cell_cycle_phases.csv`.

## Key decisions
- Calcofluor white stains cell walls and septa — intensity heterogeneity within a cell is used as the septum proxy.
- Classification is rule-based (no trained model); a supervised classifier on labeled data would improve accuracy.
- Image index is stored per cell to allow per-image breakdowns if needed.

## Caveats
- Only valid for Calcofluor-white-stained images; other staining protocols require different segmentation logic.
- Touching or overlapping cells may merge into a single region, skewing area/shape features.
- The rule thresholds (1.2× median, eccentricity 0.5) are heuristic and may need tuning per organism/magnification.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "pandas", "numpy")`. Original impl: `source` -> lift to lakeFS later.
