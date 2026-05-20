# Bulk RNA-seq DE pipeline (pydeseq2)

Reference for differential expression between two groups in a bulk RNA-seq
count matrix.

## Inputs
- `counts.tsv` (or `.csv`) — genes × samples, integer raw counts.
- `samples.tsv` — sample metadata; must have a column for the design
  factor (e.g. `condition` with values `treated` / `untreated`).

## Stages

1. **Load**
   ```python
   import pandas as pd
   counts = pd.read_csv(COUNTS_PATH, sep='\t', index_col=0)   # genes × samples
   meta   = pd.read_csv(META_PATH,   sep='\t', index_col=0)   # samples
   counts = counts[meta.index]   # align column order to metadata
   counts = counts.T            # pydeseq2 expects samples × genes
   ```

2. **Filter low-count genes**
   ```python
   keep = (counts.sum(axis=0) >= 10)
   counts = counts.loc[:, keep]
   ```

3. **Fit**
   ```python
   from pydeseq2.dds import DeseqDataSet
   from pydeseq2.default_inference import DefaultInference
   dds = DeseqDataSet(
       counts=counts.astype(int),
       metadata=meta,
       design_factors='condition',
       inference=DefaultInference(n_cpus=4),
   )
   dds.deseq2()
   ```

4. **Contrast**
   ```python
   from pydeseq2.ds import DeseqStats
   ds = DeseqStats(dds, contrast=['condition', 'treated', 'untreated'])
   ds.summary()
   results_df = ds.results_df
   ```

5. **Outputs** (one PNG each so each registers as a distinct figure entity):
   ```python
   # Volcano
   import numpy as np
   sig = (results_df['padj'] < 0.05) & (np.abs(results_df['log2FoldChange']) > 1)
   fig, ax = plt.subplots(figsize=(6, 4))
   ax.scatter(results_df['log2FoldChange'], -np.log10(results_df['padj']),
              c=np.where(sig, '#5b21b6', '#9ca3af'), s=6, alpha=0.7)
   ax.set_xlabel('log2 fold change'); ax.set_ylabel('-log10 adj. p')
   ax.set_title('Volcano: treated vs untreated')
   fig.tight_layout(); fig.savefig('volcano.png', dpi=120)

   # MA
   fig, ax = plt.subplots(figsize=(6, 4))
   ax.scatter(np.log10(results_df['baseMean'] + 1),
              results_df['log2FoldChange'],
              c=np.where(sig, '#5b21b6', '#9ca3af'), s=6, alpha=0.7)
   ax.axhline(0, c='k', lw=0.5)
   ax.set_xlabel('log10 mean counts'); ax.set_ylabel('log2 fold change')
   ax.set_title('MA plot'); fig.tight_layout(); fig.savefig('ma.png', dpi=120)

   # Top hits table CSV
   top = results_df.sort_values('padj').head(20)
   top.to_csv('top_hits.csv')
   ```

## Suggested thresholds
- `padj < 0.05` and `|log2FC| > 1` for "significant" by default.
- Filter low-count genes with `sum(counts) >= 10` across all samples; for
  larger sample counts, raise to 30–50.

## Scenario: "drop the two non-responders"
Take this pipeline's producing code, remove the named samples from
`counts` and `meta` before the `DeseqDataSet` step, re-run. The
auto-registered variant will edge back to the baseline with `variantOf`.
