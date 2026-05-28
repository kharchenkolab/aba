---
name: identify-transcription-factor-binding-sites
description: Scan a genomic sequence for binding sites of a named transcription factor by fetching its PWM from JASPAR and scoring with a PSSM threshold
when_to_use: When given a DNA sequence and a transcription factor name and asked to locate putative binding sites with positional and strand information
requires_tools: [run_python]
capabilities_needed: [biopython, requests]
keywords: [transcription factor, TFBS, PWM, PSSM, JASPAR, motif scanning, binding site, regulatory genomics]
produces: [binding_sites_table, optional_tsv_file]
domain: genetics
source: biomni:tool/genetics.py::identify_transcription_factor_binding_sites
---
# Identify Transcription Factor Binding Sites

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Query the JASPAR REST API (`https://jaspar.genereg.net/api/v1/matrix/?name={tf_name}`) to retrieve the matrix list; take the first result's `matrix_id`.
2. Fetch the PFM file (`https://jaspar.genereg.net/api/v1/matrix/{matrix_id}.pfm`) and parse with `Bio.motifs.read(handle, "jaspar")` to get the motif object.
3. Compute the PSSM from the motif; record `pssm.max` and `pssm.min` for score normalisation.
4. Call `pssm.search(Seq(sequence), threshold=threshold)` where `threshold` is an absolute PSSM score (caller supplies 0.8 as a relative fraction, but the raw search uses absolute score; note this mismatch and normalise if needed).
5. For each hit: determine strand (positive position → `+`, negative → `-`), extract the site subsequence, compute relative score `(score - min) / (max - min)`.
6. Sort hits by position; report as a markdown table and optionally write a TSV file.

## Key decisions
- The `threshold` parameter in biomni is passed directly to `pssm.search`; Biopython's `pssm.search` expects an absolute PSSM score, not a 0–1 fraction. Prefer converting: `abs_threshold = pssm.min + threshold * (pssm.max - pssm.min)`.
- Both strands are searched simultaneously by Biopython's PSSM search (negative positions = reverse strand).

## Caveats
- JASPAR API availability required; cache the PFM file locally for offline or repeated runs.
- The first JASPAR result for a name may not be the desired species or version; allow the user to specify a matrix ID directly.
- Large sequences (whole chromosomes) may be slow; consider segmenting.

## In ABA
Implement with `run_python`; `ensure_capability("biopython", "requests")`. Original impl: `source` -> lift to lakeFS later.
