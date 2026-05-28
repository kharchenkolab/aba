---
name: analyze-copy-number-purity-ploidy-and-focal-events
description: CNVkit-based tumor copy-number workflow covering CNV segmentation, heuristic purity/ploidy estimation, simplified HRD scoring, and focal amplification/deletion detection in key genes.
when_to_use: Given an indexed tumor BAM (and optionally a matched normal BAM), characterize somatic copy-number alterations, estimate tumor purity and ploidy, approximate HRD burden, and flag focal events in cancer driver genes.
requires_tools: [run_python]
capabilities_needed: [cnvkit, pandas, samtools]
keywords: [copy number, CNV, CNVkit, purity, ploidy, HRD, focal amplification, focal deletion, CDKN2A, MYC, ERBB2, cancer genomics, segmentation]
produces: [CNVkit .cnr/.cns segments, scatter/diagram plots, called .call.cns, purity/ploidy TSV summary, focal events TSV]
domain: cancer_biology
source: biomni:tool/cancer_biology.py::analyze_copy_number_purity_ploidy_and_focal_events
---
# Analyze Copy Number, Purity, Ploidy, and Focal Events

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. **CNVkit batch segmentation** — run `cnvkit.py batch` with the tumor BAM, reference FASTA, and optional normal BAM / targets BED / antitargets BED. Append `--scatter --diagram` to produce visual outputs. Output goes to `output_dir/`.

2. **Absolute copy-number calling** — if `<sample>.cns` was produced, run:
   ```
   cnvkit.py call <sample>.cns -o <sample>.call.cns -m clonal
   ```

3. **Purity & ploidy approximation** — read the `.call.cns` file with pandas; harmonize column names to lowercase. Compute:
   - `abs_cn = 2 * (2 ** log2)` per segment
   - Weighted-mean CN (by segment length) → ploidy estimate
   - Median absolute log2 deviation (MAD) → heuristic purity: `min(1.0, max(0.2, 1 - mad/1.5))`

4. **Simplified HRD scoring** from segments:
   - **LST-like**: adjacent segments both ≥10 Mb with |log2 difference| >0.2
   - **HRD-LOH-like**: segments with log2 <−0.3 and length >15 Mb
   - Composite HRD score = LST-like + HRD-LOH-like

5. **Focal event detection** — if a gene BED is provided, overlap each focal gene's coordinates with segments; take the largest-overlap segment and classify:
   - log2 ≥ `log2_amp_threshold` (default 1.0) → AMPLIFICATION
   - log2 ≤ `log2_del_threshold` (default −1.0) → DELETION
   - Default focal genes: MYC, ERBB2, CDKN2A

6. **Write outputs**: summary metrics TSV (`<sample>_cn_summary.tsv`), focal events TSV (`<sample>_focal_events.tsv`).

## Key decisions
- CNVkit is located via `shutil.which`; falls back to conda env `biomni_e1` then `bio_env_py310`.
- Purity formula is a heuristic clamp — not a model-based estimate.
- Telomeric allele imbalance (TAI) component of HRD is omitted (requires allele-specific data).
- Chromosome naming is normalized (strips `chr` prefix) for BED/segment overlap matching.

## Caveats
- Purity/ploidy estimates are rough heuristics; use ABSOLUTE, FACETS, PureCN, or Sequenza for publication-grade results.
- HRD scoring here is a simplified proxy; validated clinical HRD requires scarHRD or HRDetect with allele-specific SNP data.
- No germline filtering; somatic vs. germline CNVs are not distinguished without a matched normal.
- Panel/exome runs need targets and antitargets BED files; WGS can omit them.

## In ABA
Implement with `run_python` (subprocess to `cnvkit.py`, pandas for segment parsing); `ensure_capability(cnvkit, samtools)`. For improved purity/ploidy, chain with FACETS or PureCN. Original impl: `source` -> lift to lakeFS later.
