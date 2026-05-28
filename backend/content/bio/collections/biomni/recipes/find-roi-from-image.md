---
name: find-roi-from-image
description: Detect band ROIs in a western blot or gel image using blob detection and contour matching
when_to_use: To automatically locate band regions of interest in a blot image before densitometry with analyze-western-blot
requires_tools: [run_python]
capabilities_needed: [opencv-python, numpy]
keywords: [western blot, gel electrophoresis, ROI detection, blob detection, band segmentation, image analysis]
produces: [path to annotated image PNG, list of ROI tuples "(x, y, width, height)"]
domain: pharmacology
source: biomni:tool/pharmacology.py::find_roi_from_image
---
# Find ROI From Image

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image as grayscale with `cv2.imread`.
2. Build a binary mask: `cv2.inRange(image, lower_threshold, upper_threshold)` then `bitwise_not` to highlight dark bands on light background.
3. Configure `cv2.SimpleBlobDetector` with: minArea=120, minConvexity=0.7, minInertiaRatio=0.001, maxInertiaRatio=0.4.
4. Detect blobs (keypoints) in the mask.
5. Find contours from the mask using morphological closing (50×1 kernel to bridge horizontal gaps) then `cv2.findContours`.
6. For each keypoint, use `cv2.pointPolygonTest` to match it to a contour; compute convex hull and bounding rect with padding (5 px each side).
7. Filter ROIs by pixel texture: compute Laplacian edge strength, Sobel gradient magnitude, and std-dev in each ROI; discard text-like regions (edge > 10, gradient > 70, std > 50).
8. Annotate the original image (and mask) with red ROI rectangles; optionally draw green hulls and blue keypoint boxes in debug mode.
9. Save annotated images and return `(annotated_path, rois)`.
10. Warn if detected ROI count differs from `number_of_bands`.

## Key decisions
- Threshold values for blob filtering come from `analyze_pixel_distribution` output; caller picks appropriate lower/upper bounds.
- ROIs larger than 50% of image area are discarded as detection errors.
- Output ROI list maps directly to `target_bands` input for `analyze_western_blot`.

## Caveats
- Morphological closing kernel size (50 px) may need tuning for very low-resolution images.
- Text labels near bands can survive filtering; manual ROI review recommended.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "numpy")`. Original impl: `source` -> lift to lakeFS later.
