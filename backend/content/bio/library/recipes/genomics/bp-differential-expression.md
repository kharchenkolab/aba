---
name: bp-differential-expression
description: Best-practice scRNA-seq differential expression ACROSS CONDITIONS — pseudobulk aggregation per sample x cell type then DESeq2/edgeR/pydeseq2, avoiding pseudoreplication, per the Single-cell Best Practices book.
when_to_use: Use this for the condition-DE STAGE only — multi-sample scRNA-seq with a condition variable (stim vs ctrl) where you want genes that change between conditions within a cell type via pseudobulk (NOT a per-cell Wilcoxon). For cluster MARKER genes use rank_genes_groups (see bp-annotation); for the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, decoupler, pydeseq2]
keywords: [condition differential expression, pseudobulk DE, DESeq2, edgeR, pydeseq2, decoupler aggregation, pseudoreplication, biological replicates, MAST random effect]
produces: [pseudobulk.h5ad, de_results.csv, volcano.png]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/conditions/differential_gene_expression.html"
---

# scRNA-seq differential expression across conditions (best practice)

Question: which genes differ between **conditions** (treated vs control, disease vs healthy)
within a cell type? The book is emphatic: **use pseudobulk**, not a per-cell test. Cells from
one subject are NOT independent — naive cell-level tests (Wilcoxon, t-test) inflate the FDR via
**pseudoreplication**. The statistical unit is the **sample/subject**, not the cell.

> This is condition DE. For cluster **marker** genes (one cluster vs the rest), use
> `sc.tl.rank_genes_groups` — see **`bp-annotation`**.

**Provision:** `ensure_capability(["scanpy","anndata","decoupler","pydeseq2"])`. Needs **raw counts**
and **biological replicates** per condition (>=2-3 samples/condition; more samples >> more cells).

## Step 1 — aggregate to pseudobulk (per sample x cell type)
```python
import decoupler as dc
# sum raw counts within each (sample, cell_type) group -> a bulk-like matrix
pdata = dc.get_pseudobulk(
    adata, sample_col="sample_id", groups_col="cell_type",
    layer="counts", mode="sum", min_cells=10, min_counts=1000,
)
# drop sparse pseudo-samples (book filters cell types with <~30 cells/sample)
```

## Step 2 — pick one cell type, run a bulk DE method
The book recommends bulk methods (edgeR / DESeq2 / limma) — they beat scRNA-native methods here.

**pydeseq2 (Python):**
```python
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
sub = pdata[pdata.obs["cell_type"] == "CD14_Monocytes"].copy()
dds = DeseqDataSet(adata=sub, design_factors="condition")   # control level first via design
dds.deseq2()
res = DeseqStats(dds, contrast=["condition","stim","ctrl"]); res.summary()
res.results_df.sort_values("padj").to_csv("de_results.csv")
```
**DESeq2 / edgeR (R)** — preferred when you need the LRT, multi-factor models, covariate control,
interactions, or arbitrary contrasts (pydeseq2 is Wald-only). Use ABA's **`deseq2-r`** recipe with
the pseudobulk matrix as `countData` and the per-pseudosample table as `colData`.

## Step 3 — sanity & reporting
- MDS/PCA on the pseudobulks BEFORE testing to spot confounders (batch, donor) -> add to the design.
- BH-FDR across genes; report effect size + padj; volcano plot.

## Single-cell-native methods (secondary, with caveats)
**MAST with a random effect** for subject is acceptable; plain MAST / Wilcoxon / scVI-DE without a
subject term are prone to false positives from pseudoreplication.

## Pitfalls the book calls out
- **Pseudoreplication is the cardinal sin** — never treat cells as replicates for condition DE.
- **Power comes from samples**, not cells — adding cells per subject barely helps.
- **Raw counts only** into DESeq2/edgeR/pydeseq2 — never normalized/integrated/logged values.
- Filter sparse pseudo-samples (too few cells -> unreliable aggregation).
- Confirm contrast direction (stim vs ctrl flips the sign).

## In ABA
`decoupler` for aggregation, then **`bulk-rnaseq-de`** (pydeseq2) or **`deseq2-r`** (R, for
LRT/multi-factor/custom contrasts) on the pseudobulk. Feed the ranked DE results into
**`bp-gsea-pathway`** for enrichment.
