---
name: generate-gene-embeddings-with-esm-models
description: Generate per-gene protein embeddings by averaging ESM2 representations across all protein isoforms
when_to_use: Embed genes as fixed-length protein vectors for functional similarity analysis, clustering, or downstream ML using Meta's ESM2 language models
requires_tools: [run_python]
capabilities_needed: [fair-esm, torch, numpy, requests, tqdm]
keywords: [ESM2, protein embedding, gene embedding, isoform, language model, fair-esm, Ensembl]
produces: [PyTorch .pt file mapping Ensembl gene IDs to embedding tensors]
domain: genomics
source: biomni:tool/genomics.py::generate_gene_embeddings_with_ESM_models
---
# Generate Gene Embeddings with ESM Models

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load ESM2 model and alphabet: `model, alphabet = esm.pretrained.load_model_and_alphabet(model_name)`; move to GPU if available; set `model.eval()`.
2. For each Ensembl gene ID, fetch protein isoform sequences from the Ensembl REST API (`https://rest.ensembl.org/sequence/id/{ensembl_id}?type=protein;multiple_sequences=1`); filter to sequences `<= max_sequence_length` residues.
3. Build `all_data` list of `(gene_isoform_label, sequence)` tuples.
4. Process in batches of `batch_size` using `batch_converter`; run `model(tokens, repr_layers=[layer])` with `torch.no_grad()`; extract mean of non-BOS/EOS token representations as the isoform embedding.
5. On OOM, clear CUDA cache and retry with single-item batches.
6. Average all isoform embeddings per gene with `torch.stack(...).mean(dim=0)`.
7. Save to `save_path` (default: `esm_embeddings_{model_name}_{genes}.pt`) via `torch.save`.

## Key decisions
- `model_name` (default `"esm2_t6_8M_UR50D"`): smaller model, fast inference; use `esm2_t33_650M_UR50D` or larger for higher quality at cost of memory.
- `layer` (default 6): representation layer index; typically the last layer of the chosen model.
- `max_sequence_length` (default 1024): truncates very long isoforms to avoid memory issues.

## Caveats
- Billion-parameter ESM models require GPUs with 80 GB VRAM or FSDP across multiple GPUs.
- Ensembl REST API is queried per gene — rate-limit or cache for large gene lists.
- Genes with no isoforms under `max_sequence_length` produce no embedding.

## In ABA
Implement with `run_python`; `ensure_capability("fair-esm", "torch", "numpy", "requests", "tqdm")`. Original impl: `biomni:tool/genomics.py::generate_gene_embeddings_with_ESM_models` — lift to lakeFS later.
