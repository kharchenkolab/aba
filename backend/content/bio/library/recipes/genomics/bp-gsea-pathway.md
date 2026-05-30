---
name: bp-gsea-pathway
description: Best-practice scRNA-seq gene-set enrichment & pathway/TF activity — decoupler (GSEA/ORA/AUCell) with MSigDB, PROGENy and CollecTRI/DoRothEA, per the Single-cell Best Practices book.
when_to_use: Use this for the enrichment / pathway-and-TF-activity STAGE only — DE results (ranked stats) to enriched pathways, OR per-cell pathway/TF activity scores, via decoupler with MSigDB / PROGENy / CollecTRI. For the full rigorous flow see the scrna-best-practices index.
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, decoupler, omnipath]
keywords: [gene set enrichment, GSEA, ORA over-representation, pathway activity, decoupler, AUCell, MSigDB hallmark, PROGENy, DoRothEA, CollecTRI, transcription factor activity]
produces: [enrichment_results.csv, pathway_activity.h5ad, tf_activity_heatmap.png]
domain: genomics
source: "Single-cell Best Practices (Heumos et al.) — sc-best-practices.org/conditions/gsea_pathway.html"
---

# scRNA-seq gene-set enrichment & pathway/TF activity (best practice)

Two distinct questions, two modes:
1. **Enrichment** — given DE results, which pathways/gene-sets are over-represented? (cluster/condition level)
2. **Activity** — score absolute pathway/TF activity **per cell**, independent of any comparison.
The book centers everything on **decoupler** + curated resources (MSigDB, PROGENy, CollecTRI/DoRothEA).

**Provision:** `ensure_capability(["scanpy","anndata","decoupler","omnipath"])`.

## Get gene sets / networks
```python
import decoupler as dc
msigdb = dc.get_resource("MSigDB")
hallmark = msigdb[msigdb["collection"] == "hallmark"]          # or reactome_pathways
progeny  = dc.get_progeny(organism="human", top=500)          # pathway responsive genes
collectri = dc.get_collectri(organism="human")                # TF -> target network (DoRothEA successor)
```
Filter gene sets to ~15-500 genes present in the data — coverage drives reliability.

## Mode 1 — enrichment on DE results (cluster/condition level)
Rank by a DE statistic (t-stat / logFC from `bp-differential-expression` or `rank_genes_groups`):
```python
# build a 1-row 'stat' matrix (genes in columns) from your DE table, then:
dc.run_gsea(mat=stat_df, net=hallmark, source="geneset", target="genesymbol")
# over-representation on a gene LIST instead of a ranking:
dc.run_ora(mat=adata, net=hallmark, source="geneset", target="genesymbol")
```
For complex designs (batch/nested), the book also uses R `limma::fry` on pseudobulks
(self-contained test, handles small n).

## Mode 2 — per-cell pathway / TF activity
```python
dc.run_aucell(adata, net=hallmark, source="geneset", target="genesymbol", use_raw=False)
dc.run_mlm(adata, net=progeny, source="source", target="target", weight="weight")   # PROGENy pathway activity
dc.run_ulm(adata, net=collectri, source="source", target="target", weight="weight") # TF activity
# scores land in adata.obsm['mlm_estimate'] etc. -> sc.pl.matrixplot / heatmap by cell type
```
PROGENy (pathway footprints) and CollecTRI/DoRothEA (TF regulons) are bulk-derived but work well
on scRNA-seq. AUCell is the per-cell-optimized scorer.

## Choosing
| question | tool |
|---|---|
| pathways enriched in a DE ranking | `dc.run_gsea` |
| pathways over-represented in a gene list | `dc.run_ora` |
| per-cell pathway activity | `dc.run_aucell` / `dc.run_mlm` (PROGENy) |
| per-cell TF activity | `dc.run_ulm` (CollecTRI) |
| complex design, replicates | R `limma::fry` on pseudobulk |

## Pitfalls the book calls out
- **Normalize first** (log1p or sctransform/scran improves enrichment performance).
- **Filter gene-set size** (15-500) — sparse coverage degrades results.
- GSEA/ORA are **competitive** (vs background); fry/camera are **self-contained** (need replicates,
  account for inter-gene correlation) — pick per design.
- Mind direction: positive = up, negative = down.

## In ABA
ABA also ships **`gene-set-enrichment-analysis`** and
**`get-gene-set-enrichment-analysis-supported-database-list`**. Enrichment consumes DE output from
**`bp-differential-expression`**; per-cell activity layers onto **`bp-annotation`** for
interpreting cluster identity/state.
