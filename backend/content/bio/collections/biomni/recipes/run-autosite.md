---
name: run-autosite
description: Detect druggable binding sites on a protein and extract box center/size for docking setup
when_to_use: When no known binding site exists and a docking search box must be defined automatically
requires_tools: [run_python]
capabilities_needed: [autosite, ADFR-Suite, prepare_receptor]
keywords: [binding site detection, pocket detection, AutoSite, docking box, PDBQT]
produces: [box center coordinates, box size, AutoSite summary log]
domain: pharmacology
source: biomni:tool/pharmacology.py::run_autosite
---
# Run AutoSite for Binding-Site Detection

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Create the output directory if absent.
2. Convert the input PDB to PDBQT format: `prepare_receptor -r <pdb> -o <pdbqt>`.
3. Run AutoSite: `autosite -r <pdbqt> --spacing <spacing> -o <output_dir>`.
4. Parse `_AutoSiteSummary.log` in the output directory using regex to extract `Box center: (x, y, z)` and `Box size: (dx, dy, dz)`.
5. Return a summary string with the extracted values (or a warning if parsing fails).

## Key decisions
- Default grid spacing is 1.0 Å; smaller values improve resolution at the cost of compute.
- The log regex patterns `Box center:\s*\(([^)]+)\)` and `Box size:\s*\(([^)]+)\)` are specific to AutoSite v1.x output format.

## Caveats
- Requires ADFR Suite (`prepare_receptor`, `autosite`) installed and in PATH.
- AutoSite may return multiple candidate pockets; only the best-scored pocket box is extracted here.
- For multi-chain or unusual PDB files, `prepare_receptor` may fail; clean PDB first.

## In ABA
Implement with `run_python` (subprocess); `ensure_capability("adfr-suite", "autosite")`. Original impl: `source` -> lift to lakeFS later.
