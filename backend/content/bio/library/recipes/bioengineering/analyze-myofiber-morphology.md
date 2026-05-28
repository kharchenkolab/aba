---
name: analyze-myofiber-morphology
description: Segment and quantify morphological properties of myofibers and nuclei in multichannel muscle tissue histology images
when_to_use: When measuring fiber area, perimeter, eccentricity, solidity, and centralized-nuclei fraction from immunofluorescence or H&E muscle sections
requires_tools: [run_python]
capabilities_needed: [scikit-image, numpy, pandas, matplotlib]
keywords: [myofiber, muscle, morphology, segmentation, histology, nuclei, DAPI, alpha-actinin, eccentricity, fiber area]
produces: [myofiber_analysis.csv, labeled_myofibers.png]
domain: bioengineering
source: biomni:tool/bioengineering.py::analyze_myofiber_morphology
---
# Analyze Myofiber Morphology

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load multichannel microscopy image with `skimage.io.imread`. Extract nuclei channel and myofiber channel by index (handles both HWC and CHW layouts).
2. Enhance contrast in both channels using `skimage.exposure.equalize_adapthist`.
3. Segment nuclei:
   - Threshold with Otsu, adaptive (`threshold_local`, block_size=35), or a manual mean×1.5 heuristic.
   - Remove small objects (min_size=30), binary closing.
   - Label with `measure.label`; get region properties.
4. Segment myofibers:
   - Threshold (Otsu: block_size=101 for adaptive), remove small objects (min_size=500), binary closing with `morphology.disk(3)`.
   - Label and get region properties.
5. Count centralized nuclei: for each nucleus centroid, check if it falls inside the myofiber binary mask. Report absolute count and fraction.
6. Extract per-fiber measurements: area, perimeter, eccentricity, solidity, orientation.
7. Save per-fiber CSV and a label-overlay PNG (`skimage.color.label2rgb`).
8. Return a structured research log with summary statistics.

## Key decisions
- `nuclei_channel` and `myofiber_channel` default to 2 and 1 respectively (typical DAPI/α-Actinin setup); override for other staining panels.
- `min_size` for myofibers (500 px) filters debris but should be rescaled when image resolution differs significantly.
- Centralized nuclei detection uses centroid-in-mask; a stricter criterion would require the entire nucleus ROI to be inside the fiber.

## Caveats
- Adaptive thresholding (`threshold_local`) is recommended for uneven illumination; Otsu fails on low-contrast images.
- Tightly packed fibers may under-segment without a watershed refinement step.
- Orientation output is in radians (−π/2 to π/2 range from skimage conventions).

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "pandas", "numpy")`. Original impl: `source` -> lift to lakeFS later.
