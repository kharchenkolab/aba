---
name: segment-and-analyze-microbial-cells
description: Segment microbial cells in fluorescence microscopy images and extract morphological metrics (area, circularity, eccentricity, axis lengths)
when_to_use: When given a fluorescence microscopy image and asked to count cells or measure cell morphology without a deep learning model
requires_tools: [run_python]
capabilities_needed: [scikit-image, scipy, numpy, pandas]
keywords: [cell segmentation, fluorescence microscopy, morphology, watershed, Otsu, circularity, eccentricity, microbial imaging]
produces: [segmented cells PNG, cell metrics CSV, cell count, morphology summary, research log]
domain: microbiology
source: biomni:tool/microbiology.py::segment_and_analyze_microbial_cells
---
# Segment and Analyze Microbial Cells

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image with `skimage.io.imread`; convert to grayscale if multi-channel.
2. Smooth with `skimage.filters.gaussian(sigma=1)` to reduce shot noise.
3. Compute Otsu threshold; binarize.
4. Clean binary mask: `morphology.remove_small_objects(min_size=min_cell_size)`, binary closing with disk(2), fill holes with `ndimage.binary_fill_holes`.
5. Compute Euclidean distance transform; find local maxima as watershed seeds; label them.
6. Run `skimage.segmentation.watershed(-distance, markers, mask=binary_mask)` to separate touching cells.
7. Measure per-cell properties with `skimage.measure.regionprops_table`: area, perimeter, eccentricity, major/minor axis lengths, mean/max intensity.
8. Compute circularity = 4π × area / perimeter².
9. Save colour-label overlay (label2rgb) as PNG and metrics as CSV.
10. Return log with cell count, average area, average circularity, and size range.

## Key decisions
- Watershed with distance-transform seeds separates touching cells without requiring a trained model.
- Minimum cell size (default 50 px) filters out noise and debris.

## Caveats
- Heavily overlapping or three-dimensional colonies may under-segment.
- Pixel-unit outputs; calibrate to physical units using known pixel size (µm/px) if needed.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "scipy", "numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
