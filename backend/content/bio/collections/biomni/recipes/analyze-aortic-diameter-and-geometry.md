---
name: analyze-aortic-diameter-and-geometry
description: Measure aortic root diameter, ascending aorta diameter, tortuosity, and dilation index from cardiovascular images
when_to_use: Given a cardiovascular ultrasound or CT/MRI image and a request to quantify aortic size or geometry
requires_tools: [run_python]
capabilities_needed: [opencv-python, numpy]
keywords: [aorta, diameter, tortuosity, dilation, cardiovascular, ultrasound, CT, MRI, geometry]
produces: [annotated image PNG, measurements TXT with root diameter/ascending diameter/tortuosity/dilation index]
domain: pathology
source: biomni:tool/pathology.py::analyze_aortic_diameter_and_geometry
---
# Analyze Aortic Diameter and Geometry

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image as grayscale with `cv2.imread`; apply Gaussian blur (5x5) then CLAHE contrast enhancement.
2. Otsu threshold to get binary image; apply morphological open+close to clean noise.
3. Find external contours; select the largest as the aorta candidate.
4. Compute centroid via image moments.
5. Measure aortic root diameter: take the 20 bottommost contour points, span their x-range.
6. Measure ascending aorta diameter: filter points near the vertical midpoint (y ± 10 px), span their x-range.
7. Tortuosity index = contour arc-length / max pairwise distance of convex hull vertices.
8. Dilation index = max(diameters) / min(diameters).
9. Draw contour and centroid on color overlay; save PNG and TXT measurements to output_dir.

## Key decisions
- Largest contour is assumed to be the aorta — works for isolated vessel images; may fail in cluttered fields.
- Fmax for dilation is estimated as 2.5× sample (no true saturation calibration); report as relative index only.
- All measurements are in pixels; conversion to physical units requires known pixel spacing.

## Caveats
- CLAHE + Otsu segmentation is fragile on noisy or multi-structure CT slices; skeletonization would be more accurate.
- Tortuosity computation is O(n²) over hull points; acceptable for typical contour sizes.
- Root/ascending landmark detection is purely geometric, not anatomy-aware.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "numpy")`. Original impl: `source` -> lift to lakeFS later.
