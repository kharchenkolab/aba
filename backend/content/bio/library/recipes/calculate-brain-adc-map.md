---
name: calculate-brain-adc-map
description: Compute an apparent diffusion coefficient (ADC) map from 4D diffusion-weighted MRI data
when_to_use: When a user has a 4D DWI NIfTI file with multiple b-values and needs voxelwise ADC mapping
requires_tools: [run_python]
capabilities_needed: [nibabel, numpy, scipy]
keywords: [ADC, diffusion MRI, DWI, b-value, brain, water diffusion, stroke, tumor, NIfTI]
produces: [ADC map NIfTI file, summary statistics mean median min max std, research log]
domain: physiology
source: biomni:tool/physiology.py::calculate_brain_adc_map
---
# Calculate Brain ADC Map

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load 4D DWI NIfTI with `nibabel`; shape is (X, Y, Z, b-volumes).
2. If no mask provided, auto-generate one: voxels in the b=0 volume above 10% of mean intensity.
3. For each masked voxel, fit the monoexponential model `S = S0 * exp(-b * ADC)` using `scipy.optimize.curve_fit` with bounds `([0,0], [inf, 0.01])`.
4. Store fitted ADC values; set to 0 on fitting failure.
5. Convert ADC from mm²/s to µm²/ms (multiply by 1000) for conventional display.
6. Save the ADC map as NIfTI using `nibabel`, preserving the original affine and header.
7. Report mean, median, min, max, std over valid (> 0) ADC voxels.

## Key decisions
- BDF ODE solver analogy: `curve_fit` with bounds is used — `p0=[S0_guess, 0.001]` works for most brain tissue.
- ADC upper bound of 0.01 mm²/s excludes physiologically implausible values and aids convergence.
- Voxel-by-voxel loop is straightforward; for large volumes consider vectorizing with `np.polyfit` on log-signal.

## Caveats
- Slow for full-brain volumes without parallelization — warn user and suggest a brain mask.
- Monoexponential model underestimates ADC at very high b-values (>1500 s/mm²); biexponential may be needed.
- At least two distinct b-values required; three or more improve fit stability.

## In ABA
Implement with `run_python`; `ensure_capability(["nibabel", "numpy", "scipy"])`. Original impl: `source` -> lift to lakeFS later.
