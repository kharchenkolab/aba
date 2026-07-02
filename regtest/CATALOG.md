# Scenario catalog

Auto-generated index of all **25** scenarios (regenerate by re-running the snippet in `_make_specs.py`'s sibling, or this catalog block). v1 = single-prompt; v2 = multi-step lifecycle (see `SCHEMA.md`).

| id | domain | schema | steps | lifecycle | data | summary |
|---|---|---|---|---|---|---|
| `alphafold` | structural_biology | v1 | 1 | — | — | AlphaFold per-residue confidence (pLDDT) |
| `atac_peaks` | genomics | v2 | 3 | branch,drop | 366 KB | Call accessible chromatin peaks from ATAC-seq fragments over a 200 ... |
| `blast_seq` | sequence_analysis | v1 | 1 | — | 0 KB | Identify an unknown protein sequence |
| `bulk_de` | transcriptomics | v1 | 1 | — | 68 KB | Bulk RNA-seq differential expression with a batch covariate (R/DESeq2) |
| `cheminformatics` | cheminformatics | v1 | 1 | — | — | Drug structure + Lipinski properties |
| `colocalization` | bioimaging | v2 | 3 | delete,resume | 1.8 MB | Quantify Pearson/Manders colocalization of two markers inside cells... |
| `crispr_guides` | genome_engineering | v1 | 1 | — | — | CRISPR knockout guide design |
| `enrichment` | functional_genomics | v1 | 1 | — | 0 KB | Over-representation / pathway enrichment of a gene list |
| `foci_count` | bioimaging | v2 | 3 | revise,version_change | 1.5 MB | Segment nuclei and detect gamma-H2AX-like foci inside them in a con... |
| `gwas_popstruct` | genomics | v2 | 3 | resume,revise | 801 KB | A 400-individual, 1000-SNP cohort with two hidden subpopulations an... |
| `image_registration` | bioimaging | v2 | 3 | branch | 128 KB | Align a moving microscopy image to a fixed reference and quantify t... |
| `liftover` | genomics | v1 | 1 | — | 0 KB | Coordinate liftover hg19 → hg38 |
| `methylation_dmr` | genomics | v2 | 3 | branch,version_change | 651 KB | Find differentially methylated CpG positions between case and contr... |
| `microbiome` | microbiome | v1 | 1 | — | 4 KB | 16S alpha diversity between groups |
| `msa_phylo` | protein | v2 | 3 | branch,delete | 1 KB | Align a set of cross-species ortholog protein sequences, build a ph... |
| `nuclei_count` | bioimaging | v2 | 3 | branch,revise | 886 KB | Segment and count nuclei in a synthetic DAPI + marker fluorescence ... |
| `protein_domains` | protein | v2 | 3 | resume,revise | 1 KB | Identify a multi-domain membrane receptor from its sequence and lay... |
| `pseudobulk_de` | genomics | v2 | 3 | delete,revise | 735 KB | From a multi-sample single-cell RNA-seq cohort (3 control + 3 treat... |
| `scrna_qc_clustering` | genomics | v2 | 3 | resume,revise | 1.5 MB | Take one PBMC-like scRNA-seq sample of raw counts through standard ... |
| `scvi_integration` | genomics | v1 | 1 | — | 3.2 MB | Batch integration of two scRNA-seq samples with scVI |
| `structure_superpose` | protein | v2 | 3 | revise,version_change | — | Fetch two crystal structures of the same protein kinase in two conf... |
| `survival` | clinical_stats | v1 | 1 | — | 1 KB | Survival association + Kaplan–Meier |
| `tpm` | transcriptomics | v1 | 1 | — | 33 KB | Counts → TPM and top-expressed genes |
| `variant_annotation` | genomics | v2 | 3 | revise,version_change | 1 KB | Annotate the protein/functional consequences of ~24 real, well-docu... |
| `variant_to_structure` | protein | v2 | 3 | drop,resume | — | Given two real missense variants in human phenylalanine hydroxylase... |

## Domain coverage
| domain | count |
|---|---|
| genomics | 8 |
| bioimaging | 4 |
| protein | 4 |
| transcriptomics | 2 |
| structural_biology | 1 |
| sequence_analysis | 1 |
| cheminformatics | 1 |
| genome_engineering | 1 |
| functional_genomics | 1 |
| microbiome | 1 |
| clinical_stats | 1 |

## Lifecycle-event coverage (v2 scenarios)
How many scenarios exercise each project-lifecycle event (the realistic branch/drop/resume/revise/version-change/delete situations).

| event | scenarios |
|---|---|
| revise | 8 |
| resume | 5 |
| branch | 5 |
| drop | 2 |
| delete | 3 |
| version_change | 4 |
