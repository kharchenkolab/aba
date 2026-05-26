Pipeline guidance:
- For scRNA-seq data, prefer scanpy. Compact pipeline: read → calculate_qc_metrics → filter (n_genes ≥ 200, mt_fraction < 0.20) → normalize_total → log1p → highly_variable_genes → pca → neighbors → umap → leiden → rank_genes_groups.
- For bulk RNA-seq DE between two groups, use pydeseq2. Standard flow: load counts (genes × samples) + design CSV → filter low-count genes (sum ≥ 10) → DeseqDataSet → deseq2() → DeseqStats with the contrast → volcano + MA + top-hits table (each as its own PNG).
- When the user uploads a 10x archive, call inspect_upload first; it will tell you the format and suggest the loader.
