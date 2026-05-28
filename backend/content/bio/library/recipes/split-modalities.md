---
name: split-modalities
description: Split a 4D NIfTI file into separate per-modality 3D files using nnUNet naming convention
when_to_use: When a BraTS-style 4D NIfTI (FLAIR/T1w/T1gd/T2w stacked on axis 3) must be decomposed before nnUNet inference
requires_tools: [run_python]
capabilities_needed: [nibabel, numpy]
keywords: [NIfTI, modality splitting, BraTS, nnUNet, MRI, 4D, FLAIR, T1w, T2w]
produces: [directory of 3D NIfTI files named <case>_0000.nii.gz through <case>_0003.nii.gz]
domain: bioimaging
source: biomni:tool/bioimaging.py::split_modalities
---
# Split Modalities

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the 4D NIfTI with `nibabel.load`; call `.get_fdata()` to obtain an (X, Y, Z, 4) array.
2. Validate shape: must be 4D with exactly 4 volumes on axis 3; raise `ValueError` otherwise.
3. For each modality index i in [0, 1, 2, 3] (FLAIR, T1w, T1gd, T2w):
   - Slice `data[:, :, :, i]` to get a 3D array.
   - Wrap in a new `nib.Nifti1Image` reusing the original affine and header.
   - Save to `<output_dir>/<case_name>_{i:04d}.nii.gz`.
4. Return `output_dir`.

## Key decisions
- Modality order follows BraTS 2021 convention: index 0 = FLAIR, 1 = T1w, 2 = T1gd, 3 = T2w.
- nnUNet requires the `_XXXX` channel suffix; zero-padded to 4 digits.
- The original affine and header are preserved so spatial metadata is not lost.

## Caveats
- Only handles exactly 4-modality 4D volumes; extend the modality list for other datasets.
- Does not resample or reorient; assumes the input is already in a consistent space.

## In ABA
Implement with `run_python`; `ensure_capability("nibabel")`. Original impl: `source` -> lift to lakeFS later.
