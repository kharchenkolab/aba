---
name: prepare-input-for-nnunet
description: Normalize any NIfTI input (file or directory, 4D or pre-split) into nnUNet-ready per-channel files
when_to_use: Before calling nnUNet inference when the input format is unknown or mixed
requires_tools: [run_python]
capabilities_needed: [nibabel]
keywords: [nnUNet, input preparation, NIfTI, 4D, modality splitting, BraTS, preprocessing]
produces: [directory of nnUNet-ready NIfTI files with _XXXX channel suffix]
domain: bioimaging
source: biomni:tool/bioimaging.py::prepare_input_for_nnunet
---
# Prepare Input for nnUNet

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. If `input_path` is a file:
   - Load with `nibabel`; if shape is (X, Y, Z, 4) call `split_modalities` to decompose into `output_dir`.
   - Otherwise copy the file to `<output_dir>/<case_name>_0000.nii.gz` (single-channel case).
2. If `input_path` is a directory:
   - List all `.nii` / `.nii.gz` files.
   - If any file already matches `*_0000.nii.gz`, the directory is pre-split: copy all NIfTI files to `output_dir`.
   - Otherwise scan each file for a 4D volume and call `split_modalities` on the first one found.
   - If no 4D file exists, copy all existing NIfTI files as-is.
3. Return `output_dir`.

## Key decisions
- Heuristic: presence of `_0000.nii.gz` signals an already-prepared directory.
- Errors loading individual files during the 4D scan are silently skipped (debug-logged).
- `output_dir` is always created before any copy/write.

## Caveats
- Only the first 4D file in a directory is split; subsequent ones are ignored.
- No resampling or orientation correction is applied.

## In ABA
Implement with `run_python`; `ensure_capability("nibabel")`. Original impl: `source` -> lift to lakeFS later.
