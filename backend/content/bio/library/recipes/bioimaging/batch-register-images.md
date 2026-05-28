---
name: batch-register-images
description: Register all NIfTI images in a directory to a single fixed reference, using rigid, affine, or deformable transform
when_to_use: When aligning a cohort of images to a common atlas or template in one operation
requires_tools: [run_python]
capabilities_needed: [SimpleITK, numpy]
keywords: [batch registration, rigid, affine, deformable, SimpleITK, NIfTI, atlas, cohort, alignment]
produces: [per-image subdirectories each containing registered NIfTI and transform file, summary results dict]
domain: bioimaging
source: biomni:tool/bioimaging.py::batch_register_images
---
# Batch Register Images

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Scan `moving_images_dir` for files with extensions `.nii`, `.nii.gz`, `.nrrd`, `.mha`, `.mhd`.
2. Raise `ValueError` if no files are found.
3. For each moving image, create `<output_dir>/registration_<image_stem>/` as the individual output dir.
4. Dispatch to the appropriate registration function based on `transform_type`:
   - `"rigid"` → `quick_rigid_registration`
   - `"affine"` → `quick_affine_registration`
   - `"deformable"` → `quick_deformable_registration`
5. Store each result (or `{"error": str(e)}` on failure) in a results dict keyed by image stem.
6. Return the full results dict.

## Key decisions
- Errors for individual images are caught and recorded rather than aborting the batch.
- Image stem strips double extension for `.nii.gz` files.
- All shared registration parameters (metric, optimizer, learning rate, iterations, tolerance) are forwarded uniformly to each sub-call.

## Caveats
- Runs serially; parallelize with `concurrent.futures` for large cohorts.
- `create_visualizations` flag is passed through but actual visualization is not implemented in the referenced registration functions.

## In ABA
Implement with `run_python`; `ensure_capability("SimpleITK")`, `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
