---
name: segment-and-quantify-cells-in-multiplexed-images
description: Segment nuclei and expand to cell boundaries in multiplexed tissue images, then quantify per-cell mean intensity for each marker channel
when_to_use: Given a multichannel TIFF (e.g., IMC, CyCIF, CODEX, mIHC) and a list of marker names, to produce a spatial single-cell feature table
requires_tools: [run_python]
capabilities_needed: [scikit-image, scipy, numpy, pandas]
keywords: [multiplex imaging, spatial proteomics, cell segmentation, DAPI, nuclear segmentation, watershed, IMC, CyCIF, CODEX, per-cell quantification]
produces: [CSV spatial feature table with centroid/area/mean intensity per marker, TIFF segmentation mask (uint16)]
domain: pathology
source: biomni:tool/pathology.py::segment_and_quantify_cells_in_multiplexed_images
---
# Segment and Quantify Cells in Multiplexed Images

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load multichannel image with `skimage.io.imread`; detect layout (channels-first vs channels-last) by comparing `image.shape` to `len(markers_list)`.
2. Extract the nuclear channel (default index 0, typically DAPI).
3. Segment nuclei: Otsu threshold, remove small objects (min_size=50), binary closing (disk 2), label with `measure.label`.
4. Expand nuclei to approximate cell boundaries via watershed:
   - Seeds: labeled nuclei.
   - Topology: negative Euclidean distance transform of inverted binary nuclei (`scipy.ndimage.distance_transform_edt`).
   - Mask: binary dilation of nuclei (disk 10) to bound cell expansion.
5. Extract `measure.regionprops` on the watershed cell masks.
6. For each cell and each marker channel, compute mean intensity within the cell mask.
7. Assemble a `pandas.DataFrame` with `cell_id`, `centroid_x/y`, `area`, and `<marker>_mean_intensity` columns.
8. Save feature table as CSV and segmentation mask as uint16 TIFF.

## Key decisions
- Dilation radius of 10 px controls how far cells expand beyond nuclei — tune to expected cell size and image resolution.
- Watershed uses distance-transform topology (not marker intensity), so cell boundaries follow Voronoi-like geometry rather than membrane signal.
- Image layout is auto-detected; an explicit `nuclear_channel_index` parameter overrides the default.

## Caveats
- No membrane channel is used; cell boundary accuracy depends on nuclear size and packing density.
- Touching or overlapping nuclei may be under-split; consider using a nucleus-splitting algorithm for dense tissues.
- Very large images should be processed in tiles to avoid memory issues.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "scipy", "numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
