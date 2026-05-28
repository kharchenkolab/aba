---
name: get-uce-embeddings-scrna
description: Generate Universal Cell Embeddings (UCE) for scRNA-seq data using the Stanford UCE model
when_to_use: Embed single-cell transcriptomes into a universal latent space for cross-dataset and cross-species comparison; prerequisite for IMA cell-type mapping
requires_tools: [run_python]
capabilities_needed: [accelerate, scanpy, anndata]
keywords: [UCE, universal cell embeddings, foundation model, scRNA-seq, cross-species, single-cell]
produces: [h5ad with obsm X_uce]
domain: genomics
source: biomni:tool/genomics.py::get_uce_embeddings_scRNA
---
# Get UCE Embeddings for scRNA-seq

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Install the UCE package from `https://github.com/snap-stanford/UCE.git` if not present; add its directory to `sys.path`.
2. Check whether the output file `{data_dir}/{base_name}_uce_adata.h5ad` already exists — skip if so.
3. Build `custom_args` list: at minimum `["--adata_path", input_h5ad, "--dir", output_dir]`; append any caller-supplied extra args.
4. Parse arguments with `parse_args_uce(custom_args)`.
5. Initialize `Accelerator(project_dir=parsed_args.dir)`.
6. Run `main(parsed_args, accelerator)` — this writes the output h5ad with UCE embeddings stored in `adata.obsm["X_uce"]`.

## Key decisions
- `DATA_ROOT`: filesystem path where the UCE model weights and repository live.
- `custom_args`: pass `--model_loc` and other UCE-specific flags for non-default model variants.
- Embeddings are 1280-dim by default (33-layer, 8-epoch model).

## Caveats
- Requires the UCE repo cloned locally and model weights downloaded separately.
- GPU with substantial VRAM needed for practical throughput.
- Output must be passed to `map_to_ima_interpret_scrna` for cell-type interpretation.

## In ABA
Implement with `run_python`; `ensure_capability("accelerate", "scanpy")`. Original impl: `biomni:tool/genomics.py::get_uce_embeddings_scRNA` — lift to lakeFS later.
