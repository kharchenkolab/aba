---
name: model-bacterial-growth-dynamics
description: Simulate bacterial population dynamics over time using a logistic ODE with clearance term solved by RK45
when_to_use: When asked to model or predict bacterial growth curves given initial population, growth rate, clearance rate, and carrying capacity
requires_tools: [run_python]
capabilities_needed: [scipy, numpy, pandas]
keywords: [bacterial growth, logistic growth, ODE, carrying capacity, clearance, population dynamics, infection model, pharmacodynamics]
produces: [time-series population CSV, growth curve summary, research log]
domain: microbiology
source: biomni:tool/microbiology.py::model_bacterial_growth_dynamics
---
# Model Bacterial Growth Dynamics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Define the ODE: `dN/dt = r * N * (1 - N/K) - c * N` where r = growth rate, K = niche size (carrying capacity), c = clearance rate.
2. Solve with `scipy.integrate.solve_ivp` using the RK45 method over the specified time span with the given time step.
3. Extract time points and population trajectory from the solution.
4. Compute: max population, final population, and steady-state check (< 1% change over last 10% of simulation).
5. Save `{Time (hours), Population Size}` DataFrame to CSV.
6. Return a log with initial conditions, results summary, and CSV path.

## Key decisions
- Net effective growth = r − c; population declines to zero if clearance exceeds growth rate.
- Steady-state is defined as < 1% fractional change between 90% and 100% of simulation time.

## Caveats
- Single-compartment deterministic model; does not capture spatial heterogeneity or stochastic extinction at low counts.
- Does not model antibiotic pharmacokinetics; for drug effects, add an explicit kill term.

## In ABA
Implement with `run_python`; `ensure_capability("scipy", "numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
