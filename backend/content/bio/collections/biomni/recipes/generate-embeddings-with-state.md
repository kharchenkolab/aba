---
name: generate-embeddings-with-state
description: Generate single-cell RNA-seq embeddings using the ARC Institute SE-600M foundation model
when_to_use: Embed scRNA-seq cells with the SE-600M (State) model for downstream clustering, annotation, or atlas integration; requires GPU with 10 GB+ VRAM
requires_tools: [run_python]
capabilities_needed: [scanpy, anndata, torch, arc-state]
keywords: [SE-600M, State model, ARC Institute, foundation model, cell embedding, scRNA-seq, Hugging Face]
produces: [h5ad with obsm X_state embedding]
domain: genomics
source: biomni:tool/genomics.py::generate_embeddings_with_state
---
# Generate Single-Cell Embeddings with SE-600M (State)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load and validate input h5ad with `scanpy.read_h5ad`; warn if matrix is not CSR or if `gene_name` column is absent from `adata.var`.
2. Check for `git-lfs` (required for model download); raise an informative error if missing.
3. Download SE-600M from Hugging Face (`git clone https://huggingface.co/arcinstitute/SE-600M`) into `model_folder` unless checkpoint files already exist (checked by presence of `se600m_epoch16.ckpt` or similar).
4. Check GPU availability with `torch.cuda`; warn if VRAM < 10 GB.
5. Invoke the `arc-state` CLI: `uv run state emb transform --model-folder ... --input ... --output ... [--checkpoint ...] [--embed-key ...] [--protein-embeddings ...] [--batch-size ...]`.
6. On `CalledProcessError`, halve `batch_size` and retry up to 3 times.
7. Output h5ad has embeddings in `adata.obsm[embed_key]` (default `"X_state"`).

## Key decisions
- `batch_size` (default 500): reduce if OOM errors occur; the retry loop handles this automatically.
- `embed_key` (default `"X_state"`): change to avoid collisions with other embeddings.
- Model download is ~25 GB; ensure sufficient disk space and a fast connection.

## Caveats
- Requires `git-lfs`, `uv`, and `arc-state` CLI installed before running.
- CPU inference is extremely slow; GPU with 10+ GB VRAM is effectively required.
- Input matrix must be in CSR format and `adata.var` should have a `gene_name` column.

## In ABA
Implement with `run_python`; `ensure_capability("scanpy", "torch", "arc-state")`. Original impl: `biomni:tool/genomics.py::generate_embeddings_with_state` — lift to lakeFS later.
