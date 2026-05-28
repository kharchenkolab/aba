---
name: analyze-accelerated-stability-of-pharmaceutical-formulations
description: Model chemical and physical stability of pharmaceutical formulations under accelerated ICH storage conditions
when_to_use: Preliminary shelf-life estimation or formulation screening across temperature/humidity stress conditions
requires_tools: [run_python]
capabilities_needed: [numpy, pandas]
produces: [stability results CSV, research log with per-formulation stability assessment]
domain: pharmacology
keywords: [stability, pharmaceutical formulation, accelerated testing, Arrhenius, ICH, shelf life, degradation]
source: biomni:tool/pharmacology.py::analyze_accelerated_stability_of_pharmaceutical_formulations
---
# Analyze Accelerated Stability of Pharmaceutical Formulations

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. For each formulation × storage condition combination, compute an acceleration factor using the Q10 rule: `2^((T - 25) / 10)`.
2. Add a humidity factor if RH is provided: `1 + (RH - 60) / 100` when RH > 60%, else 1.0.
3. At each time point calculate:
   - Chemical stability (%): first-order decay `100 × exp(-0.001 × effective_time)`.
   - Physical stability (1–10 score): linear decline `10 - 0.05 × effective_time`, floored at 1.
   - Particle size change (% from baseline) for solid dosage forms only.
4. Aggregate into a pandas DataFrame; save to CSV.
5. Classify each condition as Stable / Potentially unstable / Unstable (thresholds: <90% or <7 → potentially unstable; <85% or <5 → unstable).
6. Identify the most stable formulation by average chemical stability at the final time point.

## Key decisions
- Simplified Q10-based Arrhenius approximation (not full activation-energy model); suitable for screening, not regulatory submission.
- ICH conditions (25°C/60% RH long-term; 40°C/75% RH accelerated) can be passed as `storage_conditions`.

## Caveats
- Model is empirical; real degradation may be non-first-order or dominated by specific degradation pathways.
- Particle size change is only modelled for solid dosage forms (detected via `dosage_form` field).

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
