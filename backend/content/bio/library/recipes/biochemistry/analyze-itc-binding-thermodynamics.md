---
name: analyze-itc-binding-thermodynamics
description: Fit ITC thermogram data to a one-site binding model to extract Kd, delta-H, delta-S, delta-G, and stoichiometry
when_to_use: Given ITC injection data (injection number, volume, heat) plus protein/ligand concentrations, determine binding thermodynamics
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, pandas]
keywords: [ITC, isothermal titration calorimetry, binding affinity, Kd, enthalpy, entropy, Gibbs free energy, thermodynamics, stoichiometry]
produces: [Kd, Ka, delta-H, delta-S, delta-G, stoichiometry n, R-squared]
domain: biochemistry
source: biomni:tool/biochemistry.py::analyze_itc_binding_thermodynamics
---
# Analyze ITC Binding Thermodynamics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load data from CSV/TSV path (columns: `injection`, `volume`, `heat`) or accept a numpy array directly.
2. Compute molar ratios [ligand]/[protein] accounting for cumulative dilution across injections; default cell volume = 1.4 mL.
3. Define a one-site binding model: for each injection compute differential heat from the change in bound fraction between successive injections using `Ka = 1/Kd`.
4. Fit with `scipy.optimize.curve_fit` (p0: Kd=1e-6 M, dH=-5000 cal/mol, n=1.0; maxfev=10000).
5. Compute dG = RT ln(Kd), dS = (dH - dG) / T, R-squared from residuals.

## Key decisions
- Temperature defaults to 298.15 K; user must supply actual experiment temperature for correct dG/dS.
- If protein_concentration or ligand_concentration is absent, defaults of 1.0 M and 10.0 M are used with a warning.
- One-site model only; multi-site or sequential binding require extended models.

## Caveats
- Heat-of-dilution baseline subtraction is not performed; the caller should pre-subtract blank injections.
- The simplified binding equation approximates the full Wiseman isotherm and may deviate at extreme c-values (very tight or very weak binders).
- R (gas constant) is 1.9872 cal/(mol·K); dH and dG are in cal/mol, not kcal/mol.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
