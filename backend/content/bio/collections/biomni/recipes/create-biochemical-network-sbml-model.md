---
name: create-biochemical-network-sbml-model
description: Build a validated SBML Level 3 model of a biochemical reaction network with mass-action or Michaelis-Menten kinetic laws.
when_to_use: When a user wants to encode a set of biochemical reactions and kinetic parameters into a standards-compliant SBML XML file for simulation or exchange.
requires_tools: [run_python]
capabilities_needed: [python-libsbml]
keywords: [SBML, biochemical network, kinetic model, mass action, Michaelis-Menten, systems biology, reaction network, ODE model]
produces: [SBML XML model file, research log]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::create_biochemical_network_sbml_model
---
# Create Biochemical Network SBML Model

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Create an `libsbml.SBMLDocument` at Level 3 Version 2 and add a model with ID `biochemical_network_model`.
2. Add a single default compartment (3D, constant, size 1.0).
3. Collect all unique species IDs from reactants and products across all reactions; create each as a species with `initialConcentration=0.0`, not boundary, not constant.
4. For each reaction: set ID, name, reversibility; add `SpeciesReference` entries for reactants and products with their stoichiometries.
5. Attach a `KineticLaw` from `kinetic_parameters[reaction_id]`:
   - `mass_action`: formula `k * reactant1 * reactant2 ...`
   - `michaelis_menten`: formula `Vmax * S / (Km + S)` using the first reactant
   - custom: use `formula` key directly
   - Parse formula strings with `libsbml.parseL3Formula`.
6. Run `document.checkConsistency()` (units consistency disabled) and log any errors.
7. Write to file with `libsbml.writeSBMLToFile`.

## Key decisions
- SBML L3V2 is the current standard; compatible with COPASI, tellurium, and libRoadRunner.
- Units consistency check is disabled to avoid noise from unspecified units in simple models.
- Initial concentrations default to 0; callers should set meaningful values post-hoc or extend the recipe.

## Caveats
- Custom `formula` in `kinetic_parameters` must be valid SBML L3 math syntax.
- No compartment volumes or unit definitions are set; extend for quantitative simulation accuracy.

## In ABA
Implement with `run_python`; `ensure_capability("python-libsbml")`. Original impl: `source` -> lift to lakeFS later.
