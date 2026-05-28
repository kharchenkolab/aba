---
name: analyze-pixel-distribution
description: Compute pixel intensity statistics and brightness histogram for a grayscale blot or gel image
when_to_use: Before calling find-roi-from-image to choose threshold values; also for general image QC on western blots or electrophoresis gels
requires_tools: [run_python]
capabilities_needed: [opencv-python, numpy]
keywords: [western blot, gel electrophoresis, image analysis, pixel distribution, histogram, grayscale, intensity]
produces: [image shape, min/max/mean/std intensity, percentile array, per-bucket brightness distribution dict]
domain: pharmacology
source: biomni:tool/pharmacology.py::analyze_pixel_distribution
---
# Analyze Pixel Distribution

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Read the image as grayscale with `cv2.imread(path, cv2.IMREAD_GRAYSCALE)`.
2. Compute intensity statistics: min, max, mean, std via numpy.
3. Compute percentiles at [1, 5, 10, 25, 50, 75, 90, 95, 99] using `np.percentile`.
4. Compute a 256-bin histogram with `cv2.calcHist`.
5. Aggregate histogram into brightness buckets: (0–20), (20–50), (50–80), (80–110), (110–140), (140–170), (170–200), (200–256); report pixel count and percentage for each.
6. Return a structured dict with all the above.

## Key decisions
- Fixed percentile set and bucket boundaries match the reference implementation; adjust if the image domain differs.
- Output is a dict (not a string) so callers can programmatically choose thresholds for `find_roi_from_image`.

## Caveats
- Assumes grayscale input; RGB images must be converted first.
- Dark bands on a light background will concentrate pixels in the low-intensity buckets.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "numpy")`. Original impl: `source` -> lift to lakeFS later.
