---
name: segment-with-nn-unet
description: Run nnUNet inference on NIfTI images with automatic input preparation and optional model download
when_to_use: When semantic segmentation of medical images (e.g. brain tumour, organ) is needed using a trained nnUNet task
requires_tools: [run_python]
capabilities_needed: [nnunet, nibabel, torch]
keywords: [nnUNet, segmentation, NIfTI, BraTS, brain tumour, medical image, deep learning, inference]
produces: [directory of segmentation NIfTI files]
domain: bioimaging
source: biomni:tool/bioimaging.py::segment_with_nn_unet
---
# Segment with nnUNet

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Set nnUNet environment variables: `nnUNet_RESULTS_FOLDER`, `nnUNet_raw_data_base`, `nnUNet_preprocessed` (create dirs if missing).
2. Optionally run `prepare_input_for_nnunet` into a temp subdirectory of `output_dir`.
3. Verify all `.nii`/`.nii.gz` inputs load cleanly with `nibabel`.
4. Resolve `results_folder`; fall back to `~/nnUNet_results`.
5. Construct expected model path: `<results_folder>/nnUNet/<model_type>/<task_id>/nnUNetTrainerV2__nnUNetPlansv2.1`.
6. Check model completeness: `plans.pkl` exists and at least one `fold_N/` directory contains `.model` files.
7. If model is absent and `auto_download=True`, download the zip from Zenodo (`https://zenodo.org/record/4003545/files/<task_id>.zip`), extract into the `nnUNet/` directory, then re-verify.
8. Patch `torch.load` to pass `weights_only=False` (compatibility shim); restore after inference.
9. Call `nnunet.inference.predict.predict_from_folder` with the resolved model folder, input folder, output dir, folds, TTA flag, thread counts, and mixed-precision flag.
10. Clean up the temp input directory if one was created.

## Key decisions
- Default folds: [0, 1, 2, 3, 4] (full cross-validation ensemble).
- `use_tta=False` by default for speed; enable for final results.
- `mixed_precision=True` reduces GPU memory usage.
- The torch.load patch avoids `weights_only` errors with older checkpoints.

## Caveats
- Requires a GPU for practical inference speed.
- Model download from Zenodo (~1 GB per task) needs network access and may time out.
- Only the nnUNet v1 API (`predict_from_folder`) is used here; nnUNetv2 has a different entry point.

## In ABA
Implement with `run_python`; `ensure_capability("nnunet")`, `ensure_capability("nibabel")`, `ensure_capability("torch")`. Original impl: `source` -> lift to lakeFS later.
