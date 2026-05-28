---
name: analyze-immunohistochemistry-image
description: Quantify protein expression, region count, intensity statistics, and spatial distribution from immunohistochemistry microscopy images
when_to_use: When given a tissue section IHC image and needing per-region intensity metrics, spatial clustering statistics, and a segmentation overlay
requires_tools: [run_python]
capabilities_needed: [scikit-image, numpy, scipy]
keywords: [immunohistochemistry, IHC, protein expression, tissue segmentation, spatial distribution, microscopy, image analysis, scikit-image]
produces: [segmentation PNG, region_data.csv with area and intensity per region, analysis log]
domain: immunology
source: biomni:tool/immunology.py::analyze_immunohistochemistry_image
---
# Analyze Immunohistochemistry Image

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image with `skimage.io.imread`; convert to grayscale via `skimage.color.rgb2gray` if RGB.
2. Rescale intensity to 2nd–98th percentile with `exposure.rescale_intensity` for contrast normalization.
3. Compute Otsu threshold; apply to produce binary mask; clean with `morphology.remove_small_objects` (min 50 px) and `morphology.remove_small_holes` (area 50).
4. Label connected components with `measure.label`; extract `measure.regionprops` with `intensity_image=gray_img`.
5. Compute: total weighted intensity (`sum(mean_intensity * area)`), average mean intensity, region count.
6. Collect centroid coordinates; compute all pairwise Euclidean distances with `scipy.spatial.distance.euclidean`; report mean, min, max inter-region distance.
7. Save labeled mask as `<protein>_<timestamp>_segmentation.png`.
8. Write per-region CSV (`region_id, area, mean_intensity, centroid_y, centroid_x`).

## Key decisions
- Percentile-based contrast rescaling (2–98) is more robust than full-range normalization for images with bright artifacts.
- Otsu thresholding treats brighter regions as positive; this suits DAB-stained IHC where positive signal is dark — consider inverting for brown-on-white stains.
- Pairwise distance loop is O(n²); for images with thousands of regions consider spatial indexing (scipy KD-tree).

## Caveats
- DAB/hematoxylin color deconvolution (e.g. `skimage.color.separate_stains`) would give more specific IHC quantification than grayscale intensity alone.
- Spatial distance analysis reports pixel distances; caller must supply physical pixel size to convert to µm.
- Large high-resolution whole-slide images should be tiled before processing.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "numpy", "scipy")`. Original impl: `source` -> lift to lakeFS later.
