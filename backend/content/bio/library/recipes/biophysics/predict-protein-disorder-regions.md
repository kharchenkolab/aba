---
name: predict-protein-disorder-regions
description: Predict intrinsically disordered regions (IDRs) in a protein sequence by submitting to the IUPred2A web server and parsing per-residue disorder scores.
when_to_use: When given an amino acid sequence and the goal is to identify disordered segments, linkers, or low-complexity regions relevant to protein function or interaction.
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [protein disorder, IDR, IUPred2A, intrinsically disordered, low complexity, linker, protein structure, biophysics]
produces: [CSV of per-residue position/amino-acid/disorder-score/is-disordered, research log listing disordered regions and summary statistics]
domain: biophysics
source: biomni:tool/biophysics.py::predict_protein_disorder_regions
---
# Predict Protein Disorder Regions

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Strip non-alphabetic characters from the input sequence with `re.findall(r"[A-Za-z]", seq)`.
2. POST to `https://iupred2a.elte.hu/iupred2a` with fields `seq=<sequence>`, `iupred2=long`, `anchor2=no`.
3. Parse response line by line: skip `#` comments and blank lines; for data lines extract `(position, residue, score)` as int/str/float.
4. Apply threshold (default 0.5): mark each residue `Is_Disordered = Yes/No`.
5. Identify contiguous disordered runs of length ≥ 2 residues as discrete IDRs (start, end).
6. Write per-residue CSV (`Position, Amino_Acid, Disorder_Score, Is_Disordered`).
7. Return a log with: sequence length, threshold, disordered residue count/percentage, list of IDR spans.

## Key decisions
- Uses IUPred2 "long" mode, which is appropriate for long-range disorder; "short" mode targets short disordered loops.
- ANCHOR2 is disabled; enable it to also predict protein-binding disordered regions.
- Minimum IDR length is 2 residues to exclude isolated noisy predictions.

## Caveats
- Depends on external IUPred2A server availability; no local fallback.
- Server response format may change; the parser assumes space-delimited columns with position in col 0, residue in col 1, score in col 2.
- Only the sequence is analysed; no structural or evolutionary context is incorporated.

## In ABA
Implement with `run_python`; `ensure_capability("requests")`; no local library install needed. Original impl: `source` -> lift to lakeFS later.
