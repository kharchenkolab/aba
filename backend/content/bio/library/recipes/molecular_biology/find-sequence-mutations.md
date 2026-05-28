---
name: find-sequence-mutations
description: Compare a query sequence against a reference sequence to identify substitution mutations by position
when_to_use: Use when given a pre-aligned query and reference sequence (DNA or protein) and needing a list of mutations in standard notation
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [mutation, substitution, variant, SNP, amino acid change, sequence comparison, alignment, HGVS-like notation]
produces: [mutation_list, mutation_notation]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::find_sequence_mutations
---
# Find Sequence Mutations

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate inputs; return `{mutations: [], success: False}` if any argument is missing/empty.
2. Zip `query_sequence` and `reference_sequence` pairwise (truncate to shorter).
3. For each position `i`, if `ref_base != query_base` and neither is a gap (`"-"`), record mutation as `f"{ref_base}{query_start + i}{query_base}"` (e.g., `"A123T"`).
4. Return `{mutations: [...], success: True}`.

## Key decisions
- Gap characters (`-`) in either sequence are silently skipped; designed for aligned sequences.
- `query_start` (default 1) sets the numbering offset for 1-based position reporting.
- Notation is `<ref><position><query>` matching standard single-letter amino acid or nucleotide variant notation.
- Uses `zip(..., strict=False)` so mismatched lengths truncate rather than error.

## Caveats
- Input sequences must already be aligned; this does not perform alignment itself.
- Only substitutions are detected; insertions/deletions appear as gaps and are skipped.
- For protein sequences, ensure single-letter amino acid codes are used.

## In ABA
Implement with `run_python`; no heavy third-party dependency needed beyond standard Python. `ensure_capability("biopython")` if alignment preprocessing is also required. Original impl: `source` -> lift to lakeFS later.
