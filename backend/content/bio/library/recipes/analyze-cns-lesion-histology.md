---
name: analyze-cns-lesion-histology
description: Quantify immune cell infiltration, demyelination, or IHC marker positivity in CNS tissue sections from H&E, LFB, or IHC stained microscopy images
when_to_use: When given a microscopy image of brain or spinal cord tissue and needing cell counts, damage score, myelin content, or infiltration score depending on stain type
requires_tools: [run_python]
capabilities_needed: [scikit-image, numpy]
keywords: [CNS, histology, demyelination, neuroinflammation, H&E, LFB, IHC, myelin, lesion, image analysis, scikit-image]
produces: [metrics .txt file, annotated segmentation PNG, analysis log]
domain: immunology
source: biomni:tool/immunology.py::analyze_cns_lesion_histology
---
# Analyze CNS Lesion Histology

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image with `skimage.io.imread`; convert to grayscale if RGB; enhance contrast with `exposure.equalize_adapthist`.
2. Branch by `stain_type`:
   - **H&E**: Otsu threshold on enhanced image → nuclei mask; `morphology.remove_small_objects` (min 30 px); `measure.label` + `measure.regionprops` to count cells. GLCM texture (contrast, homogeneity, energy via `feature.graycomatrix/graycoprops`) → `damage_score = contrast / (homogeneity * energy)`.
   - **LFB**: extract blue channel (index 2) for RGB; Otsu threshold → myelin mask; `myelin_percent = sum(mask) / size * 100`; `demyelination_score = 100 - myelin_percent`.
   - **IHC**: Otsu threshold on enhanced grayscale → positive stain mask; count labeled regions; `infiltration_score = positive_percent`.
3. Find region boundaries with `segmentation.find_boundaries`; overlay in red on original image; save PNG.
4. Write metrics to a timestamped `.txt` file.
5. Interpret severity thresholds: high/moderate/low for cell count (>1000/>500), damage score (>10/>5), demyelination (>70/>40 %), infiltration (>30/>15 %).

## Key decisions
- Three stain-type branches share the same pipeline skeleton; caller must specify correct stain.
- Fallback to hardcoded simulated values when scikit-image is unavailable or image path is invalid.
- Timestamp in output filenames prevents overwriting previous runs.

## Caveats
- Otsu thresholding assumes bimodal intensity histogram; non-uniform staining or out-of-focus regions degrade segmentation.
- LFB analysis requires RGB image; grayscale LFB loses color specificity.
- GLCM is computed at 256 levels on full image; for large images this is memory-intensive.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "numpy")`. Original impl: `source` -> lift to lakeFS later.
