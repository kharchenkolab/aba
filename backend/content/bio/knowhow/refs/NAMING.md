# Reference catalog naming policy

How references are named in the human-browsable `catalog/` tree of the shared
reference store (see `misc/refs.md`). The agent proposes a `structural_path`
when registering; `refstore` normalizes it with the slug rules below. Goal: a
human `ls catalog/...` tells you *what* and *where it came from* without opening
anything.

## Canonical path

```
catalog/<organism>/<assembly>/<role>/<build>/
```

- **organism** — lowercase canonical slug. Prefer the binomial with `_`:
  `homo_sapiens`, `mus_musculus`, `drosophila_melanogaster`. Common names map to
  the binomial (`human → homo_sapiens`).
- **assembly** — the source accession/release, kept verbatim-ish:
  `GRCh38.p14`, `GRCm39`, `BDGP6.46`. Dots are allowed.
- **role** — a controlled vocabulary (extend deliberately):
  `genome`, `transcriptome`, `cdna`, `gtf`, `gff`, `fai_index`,
  `star_index`, `salmon_index`, `bwa_index`, `bowtie2_index`, `hisat2_index`,
  `kallisto_index`, `bismark_index`, `vcf`, `blacklist`, `chrom_sizes`.
- **build** — what distinguishes this artifact from siblings of the same role:
  the **tool+version** for an index (`star_2.7.10a`, `salmon_1.10.1`) or the
  **source release** for raw data (`ensembl_110`). When unspecified it defaults
  to a short content hash (`sha_a1b2c3d4`) so the leaf is always unique.

## Slug rules (what `refstore` enforces)

- lowercase; spaces → `_`; keep `[a-z0-9._-]`; other runs → `-`; trim `-._`.
- A facet that's missing is dropped from the path (not left blank).
- If no facets are known, the reference lands under `misc/<sha8>/` — still
  unique, still browsable.

## Floating aliases

`latest` / `default` under a `<role>/` may point at the current preferred build.
These are the only *mutable* names; everything else is immutable and unique per
content. Aliases are managed deliberately (and, in multi-writer tiers, through
the coordinator — see `misc/refs.md §8`), never by accident.

## Acquisition provenance

The descriptor records *how* a reference was obtained — a re-runnable fetch spec
(`imported(fetch)`) or a prose recipe for non-standard/manual sources
(`imported(manual)`), or a derivation from other references (`derived_from`).
The content hash always guarantees integrity; the prose tells a human how to
re-obtain it, or that they can't. Name the *thing*; let the descriptor carry the
*story*.
