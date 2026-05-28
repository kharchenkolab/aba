---
name: simulate-thyroid-hormone-pharmacokinetics
description: Simulate thyroid hormone (T4/T3) transport, protein binding, and metabolism across tissue compartments using an ODE model
when_to_use: When a user wants to model thyroid hormone kinetics across blood, liver, thyroid, and kidney compartments over time
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, pandas]
keywords: [thyroid hormone, T4, T3, TBG, pharmacokinetics, ODE, compartmental model, hormone binding, metabolism]
produces: [concentration-time profiles for all species, peak concentrations and times, CSV results file, research log]
domain: physiology
source: biomni:tool/physiology.py::simulate_thyroid_hormone_pharmacokinetics
---
# Simulate Thyroid Hormone Pharmacokinetics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Parse `parameters` dict for `transport_rates`, `binding_constants`, `metabolism_rates`, and `volumes`.
2. Convert `initial_conditions` dict to an ordered array `y0`; retain `species_names` list for index mapping.
3. Define the ODE system: for each time step, compute:
   - Blood-to-tissue transport: bidirectional flux using `transport_rates[blood_to_<tissue>]` scaled by compartment volumes.
   - TBG binding in blood: association (`k_on_T4_TBG * T4_free * TBG`) and dissociation (`k_off_T4_TBG * complex`).
   - Hepatic T4→T3 conversion: `k_T4_to_T3_liver * T4_liver_free`.
4. Solve with `scipy.integrate.solve_ivp` using the BDF method (stiff solver), `rtol=1e-4`, `atol=1e-6`.
5. Build a DataFrame of time × species concentrations; save to CSV.
6. For all free-hormone species, report peak concentration, time to peak, and final concentration.

## Key decisions
- BDF method handles stiff kinetics typical of high-affinity protein binding (fast on/off vs slow transport).
- Index mapping via `species_names` keeps the ODE readable without hard-coded indices.
- The ODE is extensible: add more tissue compartments or binding proteins by extending the dict keys.

## Caveats
- The ODE structure handles only the species present in `initial_conditions`; missing keys are silently skipped.
- No steady-state verification; run long simulations and inspect final concentrations.
- Volume scaling is per-compartment; ensure consistent concentration units (e.g., nmol/L) throughout.

## In ABA
Implement with `run_python`; `ensure_capability(["numpy", "scipy", "pandas"])`. Original impl: `source` -> lift to lakeFS later.
