---
name: perform-pcr-and-gel-electrophoresis
description: Computationally simulate PCR amplification by locating primer binding sites in a DNA sequence and generate a synthetic agarose gel image showing the expected amplicon band
when_to_use: When given a genomic sequence and primer pair (or a target region for auto-design) and asked to predict PCR outcome, amplicon size, or visualise expected gel results
requires_tools: [run_python]
capabilities_needed: [biopython, matplotlib, numpy]
keywords: [PCR, primer, amplicon, gel electrophoresis, agarose gel, in-silico PCR, primer design, band size]
produces: [gel_image_png, amplicon_fasta]
domain: genetics
source: biomni:tool/genetics.py::perform_pcr_and_gel_electrophoresis
---
# Perform PCR and Gel Electrophoresis (Simulation)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the template DNA: read FASTA via `Bio.SeqIO` if a file path is given, otherwise use the string directly.
2. Primer preparation: if primers are not supplied, auto-design from `target_region` — take the first 20 bp as the forward primer and the reverse complement of the last 20 bp as the reverse primer.
3. Locate the forward primer in the template with `str.find`; locate the reverse primer's reverse complement (via `Bio.Seq.reverse_complement`) likewise.
4. If both sites found and forward precedes reverse, compute `amplicon_size = rev_pos + len(rev_primer) - fwd_pos` and slice the amplicon sequence; otherwise fall back to size estimate from `target_region` if provided.
5. Render a simulated gel image with matplotlib: draw a gray gel rectangle, plot a DNA ladder (100–2000 bp, log-scaled vertical positions), and add a sample band at the computed amplicon position.
6. Save gel image as `{output_prefix}_gel.png` (300 dpi) and optionally the amplicon FASTA as `{output_prefix}_amplicon.fasta`.

## Key decisions
- Band migration is modelled as `y = 10 - log2(size) / log2(2000) * 8` (log-linear approximation); adequate for visual representation.
- Primer auto-design is purely positional (no Tm or secondary-structure check); for realistic primer design use primer3 or primer-blast.

## Caveats
- This is a purely computational simulation — no actual PCR is performed.
- Does not check for off-target binding, primer dimers, or GC content.
- Fails silently if primers bind in the wrong orientation; always verify orientation before interpreting.

## In ABA
Implement with `run_python`; `ensure_capability("biopython", "matplotlib", "numpy")`. Original impl: `source` -> lift to lakeFS later.
