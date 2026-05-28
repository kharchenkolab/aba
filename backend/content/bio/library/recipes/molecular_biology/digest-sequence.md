---
name: digest-sequence
description: Simulate restriction enzyme digestion of a DNA sequence and return resulting fragments with positions
when_to_use: Use when predicting digest fragment sizes and sequences for cloning, gel verification, or mapping experiments
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [restriction digest, restriction enzyme, fragment, cloning, gel electrophoresis, cut site, RE digest]
produces: [fragment_list, cut_positions, fragment_lengths]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::digest_sequence
---
# Digest Sequence

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Create `Bio.Seq` from input; for each enzyme name, get object via `getattr(Bio.Restriction, enzyme_name)`.
2. Call `enzyme_obj.search(seq, linear=not is_circular)` to get cut positions; pool and deduplicate across all enzymes.
3. Sort cut positions. Build fragment list:
   - **No cuts**: return full sequence as single fragment.
   - **Circular**: for each consecutive pair of cut positions, extract `seq[start:end]`; the last fragment wraps: `seq[last_cut:] + seq[:first_cut]`; mark `is_wrapped=True`.
   - **Linear**: emit leading fragment (before first cut), all middle fragments, and trailing fragment (after last cut).
4. Annotate each fragment with `{fragment, length, start, end}`.
5. Sort fragments by length descending.

## Key decisions
- `Bio.Restriction` module resolves enzyme objects by name at runtime; invalid names raise AttributeError.
- Circular topology handled by joining the wrap-around fragment explicitly.
- Fragments sorted longest-first to mirror gel electrophoresis ladder reading.

## Caveats
- Cut positions from Biopython are 1-based; validate coordinate conventions if downstream code is 0-based.
- Multiple enzymes produce combined cut position list; no per-enzyme attribution in fragment output.
- Isoschizomers are treated as independent enzymes.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Original impl: `source` -> lift to lakeFS later.
