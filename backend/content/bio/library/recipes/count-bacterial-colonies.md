---
name: count-bacterial-colonies
description: Automatically count bacterial colonies in an agar plate image using watershed segmentation and report CFU concentration
when_to_use: When given an agar plate image and asked to count colonies or estimate CFU/mL
requires_tools: [run_python]
capabilities_needed: [opencv-python, scipy, numpy]
keywords: [colony counting, CFU, agar plate, watershed, image analysis, microbiology, computer vision]
produces: [colony count, CFU per mL, CFU per cm2, annotated plate image JPG, research log]
domain: microbiology
source: biomni:tool/microbiology.py::count_bacterial_colonies
---
# Count Bacterial Colonies

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the plate image with `cv2.imread`; convert to grayscale.
2. Apply Gaussian blur (7×7 kernel) to suppress noise.
3. Binarize with Otsu's adaptive threshold (inverted) to isolate dark colonies on bright agar.
4. Perform morphological opening (3×3 kernel, 2 iterations) to remove small artefacts; dilate to define sure-background.
5. Distance transform on opened mask; threshold at 50% of max to obtain sure-foreground seeds.
6. Subtract sure-foreground from sure-background to mark unknown regions.
7. Label connected components in the foreground; apply `cv2.watershed` to separate touching colonies.
8. Count unique watershed markers (excluding background label 1 and boundary label −1).
9. Calculate CFU/mL = colony_count × dilution_factor and CFU/cm² = CFU/mL / plate_area_cm2.
10. Overlay red watershed boundaries and green numbered centroids (`scipy.ndimage.find_objects`); save annotated image.
11. Return a research log with methodology, counts, and output file path.

## Key decisions
- Watershed prevents merging of touching colonies; distance transform seeds one marker per colony.
- Colony count excludes background (marker 1) and watershed boundary pixels (marker −1).
- Default plate area 65 cm² corresponds to a standard 90 mm Petri dish.

## Caveats
- Works best on uniform-background plates; mixed-background or overlapping colonies may undercount.
- Very dense plates (TNTC) will undercount due to merged watershed regions.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "scipy", "numpy")`. Original impl: `source` -> lift to lakeFS later.
