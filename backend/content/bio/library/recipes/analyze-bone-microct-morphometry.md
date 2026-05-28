---
name: analyze-bone-microct-morphometry
description: Compute standard trabecular bone morphometry parameters (BMD, BV/TV, Tb.Th, Tb.S, Tb.N) from 3D micro-CT data
when_to_use: Given a 3D micro-CT TIFF stack and a request to quantify bone microarchitecture or trabecular parameters
requires_tools: [run_python]
capabilities_needed: [scikit-image, scipy, numpy]
keywords: [micro-CT, bone morphometry, trabecular, BMD, BV/TV, Tb.Th, Tb.S, Tb.N, microarchitecture, osteoporosis, musculoskeletal]
produces: [JSON with BMD/BV/BV-TV/Tb.Th/Tb.S/Tb.N, TIFF of mid-slice segmentation]
domain: pathology
source: biomni:tool/pathology.py::analyze_bone_microct_morphometry
---
# Analyze Bone Micro-CT Morphometry

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load 3D image with `skimage.io.imread`; if 2D, expand dims to fake a single-slice volume.
2. Denoise with `scipy.ndimage.median_filter` (size=2).
3. Threshold: use provided `threshold_value` or compute `skimage.filters.threshold_otsu` on the filtered volume.
4. Create binary bone mask (`filtered > threshold`).
5. Compute parameters:
   - BMD: mean raw voxel intensity within bone mask (arbitrary HU-like units).
   - BV: count of bone voxels; BV/TV = BV / total voxels.
   - Tb.Th: mean Euclidean distance transform inside bone × 2 (diameter estimate).
   - Tb.S: mean Euclidean distance transform outside bone (in the marrow/void space).
   - Tb.N: BV/TV divided by Tb.Th (simplified plate-model approximation).
6. Save mid-volume segmentation slice as uint8 TIFF (bone = 255).
7. Write all numeric results to JSON.

## Key decisions
- Distance-transform approach for Tb.Th and Tb.S follows the standard local-thickness method but is a mean rather than a full local-thickness distribution.
- Tb.N = BV/TV / Tb.Th is the parallel-plate model approximation — consistent with standard micro-CT software conventions.
- All measurements are in voxel units; multiply by physical voxel size (µm) for absolute values.

## Caveats
- Otsu thresholding on heterogeneous bone samples may under- or over-segment soft tissue; calibrated thresholds are preferred.
- BMD in "arbitrary units" is scanner-dependent; hydroxyapatite phantom calibration is needed for mg HA/cm³.
- Large volumes require sufficient RAM; consider downsampling or chunked processing for whole-bone scans.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-image", "scipy", "numpy")`. Original impl: `source` -> lift to lakeFS later.
