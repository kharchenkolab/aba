---
name: estimate-alpha-particle-radiotherapy-dosimetry
description: Estimate absorbed doses to tumor and organs for alpha-particle radiotherapeutics using the MIRD schema
when_to_use: Preclinical dosimetry estimation for targeted alpha therapy (TAT) from biodistribution data in mice
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [dosimetry, alpha therapy, MIRD, radionuclide therapy, absorbed dose, Ac-225, biodistribution, therapeutic index]
produces: [absorbed dose per organ in Gy/MBq CSV, research log with therapeutic indices]
domain: pharmacology
source: biomni:tool/pharmacology.py::estimate_alpha_particle_radiotherapy_dosimetry
---
# Estimate Alpha-Particle Radiotherapy Dosimetry

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. For each organ, extract time-activity pairs and apply physical decay correction: `a(t) × exp(-λ·t)` where `λ = ln2 / half_life`.
2. Integrate decay-corrected activity over time using `scipy.integrate.trapezoid` to get cumulated activity (time-integrated activity, %IA·h).
3. Apply the MIRD schema: for each target organ, sum contributions from all source organs: `dose = Σ (A_source × S_factor(source→target))`.
4. Multiply by the radiation weighting factor for alpha particles (typically 20) to convert to equivalent dose.
5. Calculate therapeutic index: tumor absorbed dose / normal organ absorbed dose for each tissue.
6. Save results to CSV; return a log with per-organ doses and ratios.

## Key decisions
- S-factors (Gy/Bq·s) for each source→target organ pair must be provided externally in `radiation_parameters["S_factors"]` (e.g., from OLINDA or MIRD pamphlets).
- Dose is normalized to 1 MBq injected activity (Gy/MBq) for scaling to any clinical/preclinical activity.

## Caveats
- Trapezoidal integration underestimates AUC if time points are sparse at early/late phases; add more time points near Tmax.
- S-factors used must match the animal model geometry (mouse vs. human).
- Physical decay only is applied here; biological clearance is folded into the measured biodistribution data.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy")`. Original impl: `source` -> lift to lakeFS later.
