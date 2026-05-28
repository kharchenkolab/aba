---
name: quantify-amyloid-beta-plaques
description: Detect and measure amyloid-beta plaques in histology images using thresholding and region-property analysis
when_to_use: When a user has brain histology images (e.g., ThS or 6E10-stained) and needs plaque count, area, and intensity metrics
requires_tools: [run_python]
capabilities_needed: [scikit-image, pandas]
keywords: [amyloid beta, plaque, Alzheimer, histology, segmentation, neurodegeneration, image analysis, morphometry]
produces: [plaque count, mean and total plaque area, area fraction, mean intensity, eccentricity, CSV per-plaque data, segmented overlay PNG, research log]
domain: physiology
source: biomni:tool/physiology.py::quantify_amyloid_beta_plaques
---
# Quantify Amyloid-Beta Plaques

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image with `skimage.io.imread`; convert to grayscale via `skimage.color.rgb2gray` if multichannel; cast to uint8.
2. Smooth with `skimage.filters.gaussian(sigma=1)` to reduce shot noise.
3. Threshold using the chosen method:
   - `otsu`: `skimage.filters.threshold_otsu`
   - `adaptive`: `skimage.filters.threshold_local` (block_size=35, or adapted to image size)
   - `manual`: user-supplied value
4. Remove objects smaller than `min_plaque_size` pixels with `skimage.morphology.remove_small_objects`.
5. Label connected components with `skimage.measure.label`; clear border-touching objects with `skimage.segmentation.clear_border`.
6. Extract region properties (area, perimeter, mean intensity, eccentricity) via `skimage.measure.regionprops`.
7. Compute total plaque area and area fraction (% of tissue).
8. Save per-plaque CSV and color-overlay PNG (`skimage.color.label2rgb`).

## Key decisions
- Gaussian smoothing before thresholding reduces noise sensitivity without requiring bilateral or median filters.
- `clear_border` removes edge-truncated plaques that would give underestimated size measurements.
- Eccentricity is included because diffuse plaques are more irregular than dense-core plaques.

## Caveats
- Otsu threshold works best for bimodal intensity distributions; dense brown DAB staining may need adaptive or manual thresholding.
- `min_plaque_size` default of 50 px² is image-resolution dependent — calibrate to physical µm² when pixel size is known.
- No distinction between diffuse and dense-core plaques; add a circularity or intensity criterion to subclassify.

## In ABA
Implement with `run_python`; `ensure_capability(["scikit-image", "pandas"])`. Original impl: `source` -> lift to lakeFS later.
