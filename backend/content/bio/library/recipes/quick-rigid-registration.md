---
name: quick-rigid-registration
description: Register a moving medical image to a fixed reference using rigid (Euler3D) transformation via SimpleITK
when_to_use: When aligning images that differ only by rotation and translation (e.g. same-subject multi-session MRI)
requires_tools: [run_python]
capabilities_needed: [SimpleITK, numpy]
keywords: [registration, rigid, Euler3D, SimpleITK, NIfTI, alignment, mutual information, MRI]
produces: ["rigid_registered.nii.gz", "rigid_transform.tfm", similarity metrics dict]
domain: bioimaging
source: biomni:tool/bioimaging.py::quick_rigid_registration
---
# Quick Rigid Registration

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load fixed and moving images with `sitk.ReadImage`.
2. Optionally preprocess each: Gaussian smoothing (`sigma=1.0`) then intensity windowing to [0, 1].
3. Initialise an `sitk.Euler3DTransform` (identity).
4. Configure `sitk.ImageRegistrationMethod`:
   - Metric: Mattes mutual information (50 histogram bins), mean squares, correlation, or NCC.
   - Optimizer: gradient descent, LBFGSB, Powell, or Amoeba.
   - Interpolator: `sitk.sitkLinear`.
5. Execute registration; resample moving image onto the fixed grid with the final transform.
6. Save registered image as `rigid_registered.nii.gz`; save transform as `rigid_transform.tfm` via `sitk.WriteTransform`.
7. Compute similarity metrics before and after (MSE, Pearson correlation, NCC, mutual information via 2D histogram).
8. Return a dict with paths and both metric snapshots.

## Key decisions
- Default metric: mutual information — works across modalities.
- Default optimizer: gradient descent with `learning_rate=0.01`, 100 iterations.
- Metrics are computed in numpy from flattened arrays after masking non-finite values.

## Caveats
- Rigid registration is only appropriate when scale and shear are absent.
- No multi-resolution pyramid is configured; add `SetShrinkFactorsPerLevel` for large images.

## In ABA
Implement with `run_python`; `ensure_capability("SimpleITK")`, `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
