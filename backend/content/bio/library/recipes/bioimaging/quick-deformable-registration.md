---
name: quick-deformable-registration
description: Register a moving medical image to a fixed reference using B-spline deformable transformation via SimpleITK
when_to_use: When local tissue deformations must be captured beyond what rigid or affine transforms allow
requires_tools: [run_python]
capabilities_needed: [SimpleITK, numpy]
keywords: [registration, deformable, B-spline, SimpleITK, NIfTI, non-rigid, deformation, MRI]
produces: ["deformable_registered.nii.gz", "deformable_transform.tfm", similarity metrics dict]
domain: bioimaging
source: biomni:tool/bioimaging.py::quick_deformable_registration
---
# Quick Deformable Registration

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load fixed and moving images with `sitk.ReadImage`.
2. Optionally preprocess: Gaussian smoothing (`sigma=1.0`) then intensity windowing to [0, 1].
3. Initialise a B-spline transform via `sitk.BSplineTransformInitializer(fixed_image, [n_ctrl_pts]*3, order=3)` where `n_ctrl_pts` defaults to 4.
4. Configure `sitk.ImageRegistrationMethod` with the chosen metric and optimizer (same options as rigid/affine variants).
5. Execute registration; resample moving image onto fixed grid with linear interpolation.
6. Save registered image as `deformable_registered.nii.gz`; save transform as `deformable_transform.tfm`.
7. Compute similarity metrics (MSE, correlation, NCC, mutual information) before and after.
8. Return dict with paths and both metric snapshots.

## Key decisions
- B-spline order 3 gives smooth deformations; control point grid `[4,4,4]` is coarse — increase for finer deformations.
- Heavier optimisation budget (more iterations, smaller learning rate) typically needed vs. rigid/affine.
- Transform saved as `.tfm` (ITK format) for downstream reuse.

## Caveats
- B-spline deformable registration is non-convex; result quality is sensitive to initialisation — consider priming with a rigid/affine result.
- No diffeomorphism constraint; for topology-preserving deformations use ANTs SyN instead.

## In ABA
Implement with `run_python`; `ensure_capability("SimpleITK")`, `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
