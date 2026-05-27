---
name: bulk-rnaseq-de
description: pydeseq2 differential expression between two groups (bulk RNA-seq)
when_to_use: Bulk RNA-seq counts table with a design CSV; want DE between two conditions
requires_tools: [run_python]
produces: [de_results.csv, volcano.png, ma_plot.png, top_hits.csv]
resource_profile: small  (~10s for typical bulk study)
---

# Bulk RNA-seq DE (two-group contrast)

Standard pydeseq2 flow. Halt and present_plan with the contrast spec
before running — the user should confirm "treated vs control" not
"control vs treated", since the sign of log2FC depends on it.

## Procedure

1. Load counts (genes × samples) and design (samples × covariates).
2. Filter low-count genes: row sum >= 10.
3. `DeseqDataSet(counts=counts, metadata=design, design_factors='condition')`.
4. `dds.deseq2()`.
5. `DeseqStats(dds, contrast=['condition', 'treated', 'control']).summary()`.
6. Emit:
   - `de_results.csv` — full table sorted by padj
   - `volcano.png` — log2FC vs −log10(padj), labelled top 10
   - `ma_plot.png` — base-mean vs log2FC
   - `top_hits.csv` — padj < 0.05 sorted by |log2FC|

## Common adjustments

- **Shrinkage** — apeGLM is the default; LFC shrinkage helps
  interpretation but can be skipped if the user asks for raw effects.
- **FDR threshold** — 0.05 default; some fields prefer 0.01 or 0.1.
- **Independent filtering** — pydeseq2 does this by default.

## Caveats to mention

- A single contrast is fine for two-arm studies. For factorial designs,
  run separate DE per contrast and combine.
- If the design CSV has missing covariates for some samples, those
  samples are silently dropped — flag the count if it happens.
