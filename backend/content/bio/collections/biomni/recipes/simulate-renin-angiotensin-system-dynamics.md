---
name: simulate-renin-angiotensin-system-dynamics
description: Simulate time-dependent concentrations of renin-angiotensin system (RAS) components using a six-species ODE model with feedback
when_to_use: Given initial RAS component concentrations, kinetic rate constants, and feedback parameters, simulate system dynamics over a specified time window
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, pandas]
keywords: [renin-angiotensin system, RAS, angiotensin, ACE, ACE2, ODE, pharmacology, cardiovascular, feedback, hypertension]
produces: [ras_simulation_results.csv, final concentrations for all six RAS components]
domain: systems_biology
source: biomni:tool/systems_biology.py::simulate_renin_angiotensin_system_dynamics
---
# Simulate Renin-Angiotensin System Dynamics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Define six-species ODE system: renin, angiotensinogen, angiotensin I, angiotensin II, ACE2-angiotensin II complex, angiotensin 1-7.
2. Renin production rate includes negative feedback: `k_ren / (1 + fb_ang_II * [Ang II])`. Angiotensinogen production is constant (`k_agt`).
3. Cascade conversions: `[Ang I]` formed from renin × angiotensinogen; `[Ang II]` from `k_ace * [Ang I]`; ACE2 complex from `k_ace2 * [Ang II]`; angiotensin 1-7 from ACE2-Ang II complex.
4. Clearance terms are first-order per species; AT1R binding adds to Ang II clearance; Mas receptor adds to Ang 1-7 clearance.
5. Solve with `scipy.integrate.solve_ivp` (RK45, rtol=1e-6); save results DataFrame (time + 6 components) to CSV.

## Key decisions
- RK45 chosen as the system is not typically stiff at physiological parameters; switch to LSODA if numerical instability occurs.
- Feedback only on renin production (short-loop feedback via Ang II); long-loop and aldosterone pathways are not included.
- All clearance rates are simplified first-order; real pharmacokinetics involve saturable enzymes.

## Caveats
- Model is a minimal pedagogical RAS representation; does not include aldosterone, AT2R, baroreceptor reflexes, or renal handling.
- Units of concentration and time must be consistent; the implementation does not enforce unit checking.
- fb_ace2 feedback parameter is accepted but not used in the current ODE formulation (present for API compatibility).

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
