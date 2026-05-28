---
name: liftover-coordinates
description: Convert a single genomic coordinate between hg19 and hg38 genome builds using pyliftover chain files.
when_to_use: When the user has a genomic position in one human genome build (hg19 or hg38) and needs the equivalent coordinate in the other build — e.g. to cross-reference GWAS hits, variant annotations, or BED regions across assemblies.
requires_tools: [run_python]
capabilities_needed: [pyliftover]
keywords: [liftover, coordinate conversion, hg19, hg38, genome build, assembly, chain file, genetics]
produces: [lifted chromosome, position, strand (returned as a log string)]
domain: genetics
source: biomni:tool/genetics.py::liftover_coordinates
---
# Liftover Coordinates

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. **Load chain files** from `data_path/liftover/`:
   - `hg19ToHg38.over.chain.gz` → `LiftOver` object for hg19→hg38
   - `hg38ToHg19.over.chain.gz` → `LiftOver` object for hg38→hg19
2. **Select direction**: choose the appropriate `LiftOver` object based on `input_format` / `output_format`. Only hg19↔hg38 is supported; return an error string for any other pair.
3. **Convert**: call `lo.convert_coordinate(f"chr{chromosome}", position)`.
4. **Parse result**: if the returned list is non-empty, extract `(chrom, pos, strand)` from `result[0]`; otherwise report failure.
5. Return all steps and the final lifted coordinate as a multi-line string.

## Key decisions
- Chain files must be pre-downloaded to `data_path/liftover/`; pyliftover reads them directly (no network call at runtime).
- Chromosome is prefixed with `chr` before the call — input should be bare (e.g. `"1"`, `"X"`).
- Only single-coordinate conversion; for bulk liftover of BED files use the UCSC liftOver CLI or CrossMap instead.

## Caveats
- Only hg19↔hg38 is supported by this recipe; other assemblies (GRCh37, mm10, etc.) require different chain files and are not handled.
- A small fraction of positions cannot be lifted (deletions, unaligned regions) — check for empty result.
- pyliftover is a pure-Python re-implementation; for high-throughput use, prefer the UCSC `liftOver` CLI or CrossMap.
- Chain files are large (~50 MB each); ensure they are cached in the data lake before running.

## In ABA
Implement with `run_python`; `ensure_capability(["pyliftover"])`. Chain files must exist at the declared `data_path`. Original impl: `biomni:tool/genetics.py::liftover_coordinates` → lift to lakeFS later.
