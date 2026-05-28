---
name: quantify-corneal-nerve-fibers
description: Segment and quantify immunofluorescence-labeled corneal nerve fibers to measure density, count, length, and width
when_to_use: Given an immunofluorescence image of corneal nerves (e.g., βIII-tubulin, SP, L1CAM) and a request to quantify fiber density or morphology
requires_tools: [run_python]
capabilities_needed: [scikit-image, numpy]
keywords: [corneal nerve, nerve fiber, immunofluorescence, density, morphometry, ophthalmology, neuropathy, segmentation]
produces: [segmented binary image PNG, CSV with fiber area/density/count/average length/average width]
domain: pathology
source: biomni:tool/pathology.py::quantify_corneal_nerve_fibers
---
# Quantify Corneal Nerve Fibers

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image with `skimage.io.imread`; convert to grayscale if RGB via `skimage.color.rgb2gray`.
2. Normalize pixel values to [0, 1].
3. Threshold by chosen method:
   - `otsu`: `skimage.filters.threshold_otsu`
   - `adaptive`: `skimage.filters.threshold_local` (block_size=35)
   - `manual`: fixed value 0.5
4. Create binary mask; remove small objects (min_size=50 px) with `skimage.morphology.remove_small_objects`.
5. Fill gaps with `morphology.closing` using a disk(2) structuring element.
6. Compute fiber density = (fiber pixel area / total pixels) × 100%.
7. Label connected components with `measure.label`; extract `regionprops` for each fiber segment.
8. Average major_axis_length (length proxy) and minor_axis_length (width proxy) across all segments.
9. Save binary mask as ubyte PNG via `skimage.util.img_as_ubyte`; write metrics to CSV.

## Key decisions
- Major/minor axis lengths from `regionprops` are ellipse-fit approximations; branching fibers will be split into multiple objects.
- min_size=50 px removes noise but may discard thin short fibers — adjust based on resolution and expected fiber morphology.
- No pixel-to-micron conversion is applied; physical units require the image's known pixel spacing.

## Caveats
- Adaptive thresholding (block_size=35) may over-segment in low-SNR images; Otsu is safer as a default.
- Branching fiber networks are better analyzed with skeletonization (e.g., `skimage.morphology.skeletonize`) for total length rather than per-object major axis.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "numpy")`. Original impl: `source` -> lift to lakeFS later.
