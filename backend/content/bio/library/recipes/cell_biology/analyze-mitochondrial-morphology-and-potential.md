---
name: analyze-mitochondrial-morphology-and-potential
description: Quantify mitochondrial network morphology (fragmentation, branching, connectivity) from a morphology fluorescence image and co-register a membrane-potential image for intensity comparison.
when_to_use: When given two fluorescence images — one for mitochondrial morphology (e.g., MTS-GFP) and one for membrane potential (e.g., TMRE) — and the goal is to measure mitochondrial network structure and energetic state.
requires_tools: [run_python]
capabilities_needed: [scikit-image, opencv, scipy, numpy]
keywords: [mitochondria, morphology, membrane potential, fragmentation, skeletonization, fluorescence microscopy, TMRE, MTS-GFP, network connectivity]
produces: [binary_mitochondria.png, skeleton.png, research log with fragment count, junction count, connectivity, fragmentation index]
domain: cell_biology
source: biomni:tool/cell_biology.py::analyze_mitochondrial_morphology_and_potential
---
# Analyze Mitochondrial Morphology and Potential

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load both images with `skimage.io.imread`; convert to greyscale via `cv2.cvtColor` if multichannel; normalise to float with `skimage.util.img_as_float`.
2. Morphology channel:
   a. Gaussian denoise (sigma=1); Otsu threshold.
   b. `morphology.remove_small_objects(min_size=20)` → binary mask saved as `binary_mitochondria.png`.
   c. Skeletonise with `morphology.skeletonize` → `skeleton.png`.
   d. Label skeleton branches: `ndimage.label(skeleton)` → `num_branches`.
   e. Detect junctions: convolve skeleton with a 3×3 all-ones kernel (centre=0); pixels with neighbour count > 2 are junction points.
   f. Label binary objects: `ndimage.label(binary_img)` → `num_objects`, object sizes.
   g. Compute `connectivity = num_junctions / num_branches` and `fragmentation = 1 / (mean_size / max_size)`.
3. Potential channel: processed separately (normalised, Otsu thresholded, small objects removed); mean potential intensity extracted over the morphology mask for co-registration.
4. Save metrics and images to `output_dir`.

## Key decisions
- Skeletonisation enables network-level metrics (branching, connectivity) beyond simple object counting.
- The fragmentation index (inverse of mean-to-max size ratio) increases as the network breaks into smaller fragments.
- Morphology and potential images are treated independently; spatial co-registration at pixel level is implicit (same field).

## Caveats
- Gaussian sigma and minimum object size are fixed; densely packed mitochondria may require parameter tuning.
- No watershed separation of touching mitochondria; fused networks inflate object sizes.
- Membrane-potential quantification is intensity-based and sensitive to photobleaching and staining variability.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "opencv-python", "scipy", "numpy")`. Original impl: `source` -> lift to lakeFS later.
