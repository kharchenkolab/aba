---
name: find-enriched-motifs-homer
description: Discover de novo and known DNA sequence motifs enriched in a set of genomic peak regions using HOMER findMotifsGenome.pl.
when_to_use: Given a BED file of peak regions (e.g., from ChIP-seq or ATAC-seq), find overrepresented transcription-factor binding motifs relative to random or user-supplied background sequences.
requires_tools: [run_python]
capabilities_needed: [homer]
keywords: [motif discovery, de novo motif, transcription factor, HOMER, ChIP-seq, ATAC-seq, enrichment, binding site]
produces: [homerResults.html, knownResults.html, knownResults.txt, motif*.motif PWM files]
domain: genomics
source: biomni:tool/genomics.py::find_enriched_motifs_with_homer
---
# Enriched Motif Discovery with HOMER

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. Create the output directory.
2. Build the `findMotifsGenome.pl` command:
   ```
   findMotifsGenome.pl <peak_file> <genome> <output_dir> \
     -size 200 \
     -len <motif_length> \
     -S <num_motifs> \
     -p <threads> \
     [-bg <background_file>]
   ```
   - `-size 200`: analyse a 200 bp window centred on each peak summit.
   - `-len`: comma-separated list of motif widths, e.g. `8,10,12`.
   - `-S`: number of de novo motifs to return (default 10).
   - `-p`: parallelism (default 4 threads).
   - `-bg`: optional user-supplied background BED; omit to let HOMER auto-generate GC-matched background.
3. Run the command; capture stdout/stderr.
4. Parse results:
   - Check for `homerResults.html` (de novo motifs) and `knownResults.html`/`knownResults.txt` (known TF motifs).
   - Glob `motif*.motif` files; read each header line to extract consensus sequence, TF match, and p-value.
   - Report top 5 de novo motifs and top 3 known motifs from `knownResults.txt`.

## Key decisions
- Default genome `hg38`; pass `mm10`, `hg19`, etc. as needed — HOMER must have the genome installed.
- `-size 200` is a reasonable default for TF ChIP-seq; use `-size given` to use exact BED coordinates.
- Background is HOMER-generated (GC-matched random) unless `background_file` is provided.
- Motif header format: `>CONSENSUS\tTF_NAME/Source\tp-value\tlog-odds\t...`

## Caveats
- HOMER must be installed and its genome data directory pre-configured (`perl configureHomer.pl -install <genome>`).
- Large peak sets (>50 k regions) substantially increase runtime; consider subsampling.
- The binary is a Perl script (`findMotifsGenome.pl`), not a Python package — must be on `PATH`.
- No timeout is set in the original; wrap with `timeout` for pipeline safety.

## In ABA
Implement with `run_python` (subprocess shell-out to Perl script); `ensure_capability("findMotifsGenome.pl")`.
Original impl: `biomni:tool/genomics.py::find_enriched_motifs_with_homer` → lift to lakeFS later.
