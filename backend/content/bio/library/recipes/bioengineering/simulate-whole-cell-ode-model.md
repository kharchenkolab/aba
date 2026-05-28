---
name: simulate-whole-cell-ode-model
description: Numerically integrate a system of ODEs representing whole-cell biochemical dynamics (gene expression, metabolism, ATP)
when_to_use: When simulating time-course dynamics of a cell model defined by ODEs — e.g., mRNA/protein/metabolite/ATP coupled equations or a custom user-supplied system
requires_tools: [run_python]
capabilities_needed: [numpy, pandas, scipy]
keywords: [ODE, whole-cell model, systems biology, gene expression, metabolism, ATP, numerical integration, simulation, LSODA, BDF, RK45]
produces: [whole_cell_simulation_results.csv]
domain: bioengineering
source: biomni:tool/bioengineering.py::simulate_whole_cell_ode_model
---
# Simulate Whole-Cell ODE Model

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept initial conditions as a dict (variable_name → value) or array. Convert to a list of floats; store variable names for output labeling.
2. Accept parameters as a dict passed through to the ODE function at each evaluation step.
3. If no `ode_function` is provided, use a built-in 4-variable whole-cell model:
   - State: mRNA, Protein, Metabolite, ATP.
   - Equations: mRNA driven by transcription minus degradation; Protein synthesis proportional to mRNA × ATP; Metabolite produced by Protein; ATP produced from Metabolite, consumed by translation and basal turnover.
4. Set up time evaluation array via `np.linspace(time_span[0], time_span[1], time_points)`.
5. Integrate with `scipy.integrate.solve_ivp`, wrapping the ODE function as `lambda t, y: ode_function(t, y, parameters)`. Method defaults to LSODA (stiff-capable); allow RK45 or BDF.
6. Check `solution.success`; if successful, build a pandas DataFrame (Time + one column per variable). Save to a timestamped CSV.
7. Report final state values and solver diagnostics (nfev, njev, nsteps) in the research log.

## Key decisions
- LSODA is the default because whole-cell models often contain stiff subsystems; switch to RK45 for non-stiff, pure-diffusion models.
- The built-in default model is illustrative; real use cases should supply a custom `ode_function` validated against literature parameters.
- Initial conditions as a dict preserves semantic variable names in output CSV headers.

## Caveats
- Very stiff systems may require tight tolerances (`rtol`, `atol`); expose these as parameters if solves diverge.
- Custom ODE functions must follow the `f(t, y, params)` signature; standard `solve_ivp` functions use `f(t, y)` — the lambda wrapper handles the mismatch.
- Simulation does not include stochastic effects; for low-copy-number species, use Gillespie / tau-leaping methods instead.

## In ABA
Implement with `run_python`; `ensure_capability("scipy", "numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
