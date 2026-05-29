Pipeline guidance:
- For scRNA-seq data, prefer scanpy. Compact pipeline: read → calculate_qc_metrics → filter (n_genes ≥ 200, mt_fraction < 0.20) → normalize_total → log1p → highly_variable_genes → pca → neighbors → umap → leiden → rank_genes_groups.
- For bulk RNA-seq differential expression, read a DE recipe first rather than coding from memory (the API + count orientation differ by tool): `deseq2-r` (R/Bioconductor DESeq2 — authoritative; Wald + LRT, lfcShrink, covariate control, interactions, custom contrasts) or `bulk-rnaseq-de` (pydeseq2, Python; Wald-only, no LRT). Pick by the session's language or what the user asked for, then `read_skill` the matching one.
- When the user uploads a 10x archive, call inspect_upload first; it will tell you the format and suggest the loader.
