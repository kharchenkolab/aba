---
name: analyze-cell-morphology-and-cytoskeleton
description: Segment cells from a fluorescence microscopy image, measure morphological shape descriptors, and quantify cytoskeletal fiber alignment using Canny edge detection and Hough transform.
when_to_use: When given a fluorescence microscopy image and the goal is to quantify cell shape (area, aspect ratio, circularity, eccentricity) and cytoskeletal organisation (fiber count, mean orientation, alignment order parameter).
requires_tools: [run_python]
capabilities_needed: [scikit-image, opencv, numpy, pandas]
keywords: [cell morphology, cytoskeleton, actin fibers, fluorescence microscopy, Hough transform, cell segmentation, shape analysis, order parameter, alignment]
produces: [cell_morphology_data.csv, fiber_orientation_data.csv, cell_segmentation.png, research log]
domain: biophysics
source: biomni:tool/biophysics.py::analyze_cell_morphology_and_cytoskeleton
---
# Analyze Cell Morphology and Cytoskeleton

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load with `skimage.io.imread`; convert RGB to greyscale via `skimage.color.rgb2gray`; enhance contrast with `exposure.equalize_hist`.
2. Segment cells using the chosen method (`otsu`, `adaptive` with block_size=35/offset=0.05, or `manual` at 0.5).
3. Clean binary mask: `remove_small_objects(100)`, `remove_small_holes(100)`, `binary_closing(disk(3))`; label with `measure.label`.
4. Extract per-cell properties via `measure.regionprops_table`: area, perimeter, major/minor axis, eccentricity, orientation, solidity.
5. Derive `aspect_ratio = major/minor` and `circularity = 4π·area/perimeter²`.
6. Cytoskeleton analysis: `feature.canny(sigma=2)` → `cv2.HoughLinesP(threshold=10, minLineLength=10, maxLineGap=5)` → compute angle per line via `arctan2`.
7. Normalise angles to −90..90°; compute the nematic order parameter `S = sqrt(mean(cos2θ)² + mean(sin2θ)²)` (1=aligned, 0=random).
8. Save cell morphology CSV, fiber orientation CSV, and labelled segmentation PNG.

## Key decisions
- Histogram equalisation before thresholding improves robustness across imaging conditions.
- Three segmentation methods are offered; Otsu is the default and works well for most fluorescence images.
- Order parameter uses the nematic (2θ) convention appropriate for undirected fibers.

## Caveats
- Hough line detection is sensitive to the threshold and minimum line length; dense or curved fibers may be under-detected.
- `equalize_hist` can over-enhance noisy backgrounds; `equalize_adapthist` (CLAHE) may be preferable for heterogeneous images.
- Touching cells are not separated; watershed would improve cell count accuracy.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "opencv-python", "pandas", "numpy")`. Original impl: `source` -> lift to lakeFS later.
