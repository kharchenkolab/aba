---
name: simulate-microbial-population-dynamics
description: Stochastic Gillespie simulation of multi-species microbial populations with logistic growth, reporting extinction probabilities and trajectories
when_to_use: When asked to model stochastic microbial population dynamics, estimate extinction risk, or compare species persistence under different growth/clearance parameters
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [Gillespie algorithm, stochastic simulation, microbial population, extinction probability, logistic growth, population dynamics, stochastic ecology]
produces: [extinction probabilities per species, median extinction times, average population trajectories, research log]
domain: microbiology
source: biomni:tool/microbiology.py::simulate_microbial_population_dynamics
---
# Simulate Microbial Population Dynamics (Gillespie)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. For each of `num_simulations` runs:
   a. Initialize population array from `initial_populations`.
   b. At each step compute adjusted growth rates (logistic: `r_i * N_i * (1 - N_i/K_i)`) and death rates (`c_i * N_i`) for all species.
   c. Draw time to next event from `Exp(1/total_rate)` (Gillespie tau-leaping).
   d. Select the event (birth or death for species i) proportional to its rate.
   e. Record population at pre-defined time grid points.
   f. Track extinction events (population hitting 0) and times.
2. Average trajectories across all simulations.
3. Compute per-species extinction probability = extinctions / num_simulations.
4. Compute median/mean extinction time and 95% CI (Student-t via `scipy.stats`) where enough data exists.
5. Return a log with species parameters, extinction analysis, and trajectory summary.

## Key decisions
- Logistic growth prevents unbounded population explosion.
- Gillespie exact SSA correctly handles discrete birth/death at small population sizes.
- Simulations where a species never goes extinct contribute `None` to extinction time lists (excluded from statistics).

## Caveats
- Computationally expensive for large populations or long `max_time`; consider tau-leaping or ODE approximation above ~10^4 individuals.
- No inter-species interactions (competition, predation) are modelled; use gLV ODE recipe for community interactions.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy")`. Original impl: `source` -> lift to lakeFS later.
