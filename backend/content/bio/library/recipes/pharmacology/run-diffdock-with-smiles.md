---
name: run-diffdock-with-smiles
description: Run DiffDock blind docking inference from a SMILES string against a receptor PDB
when_to_use: Predict ligand binding poses when no prior binding-site knowledge exists; generative docking via diffusion model
requires_tools: [run_python]
capabilities_needed: [docker, diffdock]
keywords: [docking, diffdock, SMILES, blind docking, pose prediction, molecular docking]
produces: [docked pose SDF files in output directory]
domain: pharmacology
source: biomni:tool/pharmacology.py::run_diffdock_with_smiles
---
# Run DiffDock with SMILES

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate that the receptor PDB file exists; create the output directory if needed.
2. Pull the pre-built DiffDock Docker image (`rbgcsail/diffdock`).
3. Optionally verify GPU availability via `nvidia-smi` inside the CUDA container.
4. Run inference: mount the PDB as `/home/appuser/input/protein.pdb` and the output dir as `/home/appuser/output`; invoke `micromamba run -n diffdock python -m inference` with `--protein_path` and `--ligand <SMILES>`.
5. Capture stdout/stderr; report success or error detail.

## Key decisions
- GPU flag `--gpus device=<N>` is added only when `use_gpu=True`; CPU fallback is supported by omitting it.
- SMILES is passed directly on the command line as the `--ligand` argument (no SDF conversion needed).

## Caveats
- Requires Docker with GPU passthrough (nvidia-container-toolkit) for practical throughput.
- Large flexible ligands or very long SMILES may fail inference silently; inspect stderr.
- Output poses are in the container-internal `/home/appuser/output` which is bind-mounted locally.

## In ABA
Implement with `run_python` (subprocess calls to docker); `ensure_capability("docker", "diffdock-container")`. Original impl: `source` -> lift to lakeFS later.
