---
name: quick-affine-registration
description: Register a moving medical image to a fixed reference using affine transformation via SimpleITK
when_to_use: When images may differ by rotation, translation, scaling, and shear (e.g. cross-subject or cross-modality alignment)
requires_tools: [run_python]
capabilities_needed: [SimpleITK, numpy]
keywords: [registration, affine, SimpleITK, NIfTI, alignment, mutual information, MRI, scaling]
produces: ["affine_registered.nii.gz", "affine_transform.tfm", similarity metrics dict]
domain: bioimaging
source: biomni:tool/bioimaging.py::quick_affine_registration
---
# Quick Affine Registration

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load fixed and moving images with `sitk.ReadImage`.
2. Optionally preprocess each: Gaussian smoothing (`sigma=1.0`) then intensity windowing to [0, 1].
3. Initialise an `sitk.AffineTransform(3)` (identity, 3D).
4. Configure `sitk.ImageRegistrationMethod`:
   - Metric: Mattes mutual information (50 bins), mean squares, correlation, or NCC.
   - Optimizer: gradient descent, LBFGSB, Powell, or Amoeba.
   - Interpolator: `sitk.sitkLinear`.
5. Execute registration; resample moving image onto the fixed grid.
6. Save registered image as `affine_registered.nii.gz`; save transform as `affine_transform.tfm`.
7. Compute similarity metrics (MSE, Pearson correlation, NCC, mutual information) before and after.
8. Return dict with paths and both metric snapshots.

## Key decisions
- Affine allows 12 DOF (rotation, translation, scaling, shear) versus 6 DOF for rigid.
- Same metric/optimizer API as quick_rigid_registration — swap transform init only.
- Metrics use 2D joint histogram (50 bins) for mutual information estimation.

## Caveats
- Affine is a global transform; local deformations require a deformable registration.
- No multi-resolution schedule — consider adding shrink/smooth factors for large volumes.

## In ABA
Implement with `run_python`; `ensure_capability("SimpleITK")`, `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
