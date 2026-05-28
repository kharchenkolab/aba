---
name: generate-transcriptformer-embeddings
description: Generate cell or contextual gene embeddings for scRNA-seq data using the Transcriptformer foundation model
when_to_use: Embed single cells or genes with Transcriptformer (tf-sapiens, tf-exemplar, or tf-metazoa) for atlas-scale integration or cross-species analysis
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, torch, pandas, transcriptformer]
keywords: [Transcriptformer, foundation model, cell embedding, scRNA-seq, Ensembl, cross-species, tf-sapiens]
produces: [h5ad file with Transcriptformer cell or gene embeddings]
domain: genomics
source: biomni:tool/genomics.py::generate_transcriptformer_embeddings
---
# Generate Transcriptformer Embeddings

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Set `checkpoint_path` (default `./checkpoints/{model_type}`); build input/output paths.
2. Check GPU with `torch.cuda`; warn if absent.
3. If model not yet downloaded (checked by presence of `config.yaml` and `pytorch_model.bin`), run `transcriptformer download {model_type}` via subprocess with streaming output.
4. Load AnnData with `sc.read_h5ad`; detect Ensembl ID patterns in `adata.var.index` using regexes for human, mouse, zebrafish, fly, worm, yeast.
5. Ensure `adata.var["ensembl_id"]` column exists: copy from `"gene_ids"` column, or use the index if it matches an Ensembl pattern; raise if no valid IDs found.
6. Ensure `adata.raw` is set (Transcriptformer expects raw counts); set `adata.raw = adata` if absent.
7. Optionally remove duplicate genes (`remove_duplicate_genes=True`) by stripping version suffixes and keeping the first occurrence.
8. Write prepared AnnData to a temp file; run `transcriptformer inference --checkpoint-path ... --data-file ... --output-path ... --output-filename ... --batch-size ... --gene-col-name ... --precision ... [--emb-type cell|cge] [--num-gpus ...] ...`.
9. Output h5ad contains cell embeddings (or contextual gene embeddings if `emb_type="cge"`).

## Key decisions
- `model_type`: `"tf-sapiens"` (human), `"tf-exemplar"` (vertebrates), `"tf-metazoa"` (broad metazoan).
- `emb_type`: `"cell"` for mean-pooled cell vectors; `"cge"` for per-gene contextual embeddings.
- `clip_counts` (default 30): caps raw counts before model input.
- `precision="16-mixed"` balances speed and accuracy; use `"32"` for reproducibility checks.

## Caveats
- Model checkpoints are several GB; requires internet and disk space.
- Gene index must contain valid Ensembl IDs; gene symbols alone are not accepted.
- `adata.X` should be raw (unnormalized) integer counts.
- GPU required for practical throughput.

## In ABA
Implement with `run_python`; `ensure_capability("transcriptformer", "scanpy", "torch", "pandas")`. Original impl: `biomni:tool/genomics.py::generate_transcriptformer_embeddings` — lift to lakeFS later.
