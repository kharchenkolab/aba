---
name: reconstruct-3d-face-from-mri
description: Generate a 3D surface mesh of facial anatomy from a NIfTI MRI scan
when_to_use: When a user has a head/neck MRI in NIfTI format and wants a 3D facial model (OBJ) or segmentation mask
requires_tools: [run_python]
capabilities_needed: [nibabel, SimpleITK, scikit-image, numpy]
keywords: [MRI, 3D reconstruction, face, segmentation, marching cubes, NIfTI, surface mesh]
produces: [NIfTI segmentation mask, OBJ 3D mesh, research log]
domain: physiology
source: biomni:tool/physiology.py::reconstruct_3d_face_from_mri
---
# Reconstruct 3D Face from MRI

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the NIfTI file with `nibabel`; fall back to `SimpleITK.ReadImage` on failure.
2. Normalize voxel intensities to [0, 1].
3. Apply CurvatureFlow smoothing (`SimpleITK`) for noise reduction (timeStep=0.125, 5 iterations).
4. Threshold the smoothed volume (normalized threshold = raw_threshold / data_range) to create a binary mask.
5. Clean small isolated regions with `SimpleITK.BinaryOpeningByReconstruction` (kernel [3,3,3]).
6. Save segmentation as `.nii.gz` via SimpleITK.
7. Run `skimage.measure.marching_cubes` (level=0.5) to extract the isosurface.
8. Write vertices, normals, and faces to an OBJ file (1-indexed faces).

## Key decisions
- Prefer nibabel for loading; SimpleITK as fallback — covers both standard NIfTI and DICOM-derived files.
- Normalize before thresholding so the `threshold_value` parameter is interpretable across scanners.
- BinaryOpeningByReconstruction removes noise without eroding the main structure.

## Caveats
- Marching cubes quality depends heavily on `threshold_value`; the default (300) suits T1 head scans but may need tuning.
- No skull-stripping is performed; the mesh includes bone and soft tissue above the threshold.
- Large volumes can be slow; consider downsampling for preview.

## In ABA
Implement with `run_python`; `ensure_capability(["nibabel", "SimpleITK", "scikit-image", "numpy"])`. Original impl: `source` -> lift to lakeFS later.
