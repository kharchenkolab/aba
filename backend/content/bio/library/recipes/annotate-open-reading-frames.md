---
name: annotate-open-reading-frames
description: Find all Open Reading Frames (ORFs) in a DNA sequence across all six reading frames
when_to_use: Use when analyzing a DNA sequence for potential protein-coding regions, including reverse complement strand analysis
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [ORF, open reading frame, ATG, start codon, stop codon, translation, CDS, reading frame]
produces: [orf_list, aa_sequences, strand_positions, summary_stats]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::annotate_open_reading_frames
---
# Annotate Open Reading Frames

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Convert input sequence to uppercase string and create a `Bio.Seq` object.
2. For each of frames 1, 2, 3 on the forward strand, scan codons starting at `offset = frame - 1`.
3. Record positions of ATG (start) and TAA/TAG/TGA (stop) codons; at each stop codon, emit an ORF for every preceding start if `len(orf) >= min_length`.
4. Convert intra-frame offsets back to original sequence coordinates (0-based start, exclusive end).
5. If `search_reverse=True`, repeat steps 2-4 on `seq.reverse_complement()` with strand="-"; convert positions back via `orig_start = seq_length - orig_end`.
6. If `filter_subsets=True`, sort longest-first and drop any ORF fully contained within a larger same-strand ORF.
7. Sort all ORFs by length descending; compute summary stats (total, forward, reverse counts, average length).
8. Translate each ORF nucleotide sequence with `Bio.Seq.translate(to_stop=True)` to produce `aa_sequence`.

## Key decisions
- Frames 1/2/3 on both strands are checked independently; reverse strand positions are remapped to forward coordinates.
- `filter_subsets` retains only the longest non-nested ORF per region (useful for cleaner gene models).
- ORF length filter `min_length` applies to nucleotide length (include start + stop codons).

## Caveats
- No intron-awareness; designed for prokaryotic/viral sequences or cDNA.
- Reverse strand coordinate mapping assumes no wrapping (linear sequences); extend logic for circular genomes.
- All six frames searched independently; overlapping ORFs in different frames are all reported unless filtered.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Original impl: `source` -> lift to lakeFS later.
