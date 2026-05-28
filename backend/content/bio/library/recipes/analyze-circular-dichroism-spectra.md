---
name: analyze-circular-dichroism-spectra
description: Analyze CD spectroscopy data to determine secondary structure and thermal stability of proteins or nucleic acids
when_to_use: Given wavelength/CD-signal arrays (and optionally temperature melt data), determine secondary structure class and melting temperature
requires_tools: [run_python]
capabilities_needed: [numpy, matplotlib]
keywords: [CD spectroscopy, circular dichroism, secondary structure, alpha helix, beta sheet, G-quadruplex, melting temperature, Tm, thermal denaturation]
produces: [structure classification, Tm estimate, cooperativity assessment, spectral data TSV, thermal denaturation TSV]
domain: biochemistry
source: biomni:tool/biochemistry.py::analyze_circular_dichroism_spectra
---
# Analyze Circular Dichroism Spectra

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept `wavelength_data` and `cd_signal_data` as arrays; optionally `temperature_data` and `thermal_cd_data` for melt analysis.
2. For proteins: count signal points in 190–195 nm (alpha), 215–220 nm (beta), 195–200 nm (random-coil) windows; classify by whichever count dominates.
3. For nucleic acids: check for positive signal at 290–300 nm (G-quadruplex) or 270–280 nm (B-form DNA).
4. If temperature melt data provided: normalize thermal signal to 0–1 unfolded fraction; find index closest to 0.5 for Tm; assess cooperativity by transition width (<20% range = highly cooperative, <40% = moderate, else non-cooperative).
5. Write spectral TSV (wavelength, signal) and, if melt data present, thermal TSV (temp, signal, unfolded fraction) to `output_dir`.

## Key decisions
- Classification is purely heuristic (window-counting), not deconvolution — appropriate for rapid screening.
- Cooperativity metric uses fraction of data points in the 20–80% unfolded window relative to total temperature range.
- numpy is the only required library for the core logic; matplotlib only needed if plots are desired.

## Caveats
- No quantitative secondary-structure decomposition (no SELCON/CDSSTR); results are qualitative classifications.
- Tm estimate requires monotonic or approximately monotonic thermal signal; non-sigmoidal melts will give misleading values.
- Nucleic acid classification does not distinguish parallel vs antiparallel G-quadruplexes.

## In ABA
Implement with `run_python`; `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
