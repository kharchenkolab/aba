---
name: align-sequences
description: Align short DNA sequences (primers) to a longer target, tolerating up to one mismatch on both strands
when_to_use: Given one or more short DNA sequences (primers, probes, sgRNAs) and a longer target sequence, find all binding positions on the forward and reverse-complement strands with 0 or 1 mismatches — used for primer validation, off-target detection, and PCR simulation
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [alignment, primer, primer binding, mismatch, reverse complement, off-target, pcr, dna, probe]
produces: [per-primer list of alignment hits: position (0-based), strand (+/-), mismatch details (position, expected base, found base)]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::align_sequences
---
# Align Sequences (Primer Binding, ≤1 Mismatch)

Distilled from a biomni implementation. In ABA, implement with the libraries
below — not biomni.

## Approach
1. Uppercase all inputs. Wrap a single primer string in a list for uniform handling.
2. For each short sequence, generate two candidates: the sequence itself (`'+'` strand) and its reverse complement (`'-'` strand). Reverse complement is computed with a simple `dict`-based complement + `reversed()` — no library call needed for this step.
3. Slide a window of `len(short_seq)` across the target. At each position, compare base-by-base and collect mismatches as `(position_in_primer, expected_base, found_base)` tuples.
4. Record a hit if `len(mismatches) <= 1`. Store `{position, strand, mismatches}`.
5. Return a dict with an `'explanation'` key (field descriptions) and a `'sequences'` list, one entry per input primer.

## Key decisions
- Mismatch tolerance is hard-coded at **≤1**; there is no parameter for stricter or looser matching.
- Uses a naive O(L × N) sliding-window scan; no suffix arrays or seed-and-extend.
- Positions are **0-based** in the target sequence.
- Reverse-complement hits report the position on the **forward** strand (same coordinate space as the target), strand=`'-'`.
- Ambiguous IUPAC bases (N, R, Y…) are not handled — they pass through the complement dict unchanged.

## Caveats
- Scales poorly for very long targets (>100 kb) or many primers; consider `Biopython`'s `pairwise2` or a k-mer index for larger inputs.
- Does not handle circular sequences — wrap-around positions must be handled externally.
- No gap support; insertion/deletion mismatches are not detected.
- Degenerate bases in primers are not expanded — use `Bio.Seq` IUPAC tools if needed.

## In ABA
Implement with `biopython` (for `Bio.Seq` reverse complement if preferred over the inline dict); `ensure_capability("biopython")`. Original impl: `biomni:tool/molecular_biology.py::align_sequences` → lift to lakeFS later.
