---
name: analyze-protein-phylogeny
description: Perform multiple sequence alignment and phylogenetic tree construction from a set of protein sequences, with visualization of the resulting tree
when_to_use: When given a set of protein sequences in FASTA format and asked to infer their evolutionary relationships, build a phylogenetic tree, or visualise clade structure
requires_tools: [run_python]
capabilities_needed: [biopython, matplotlib]
keywords: [phylogeny, phylogenetic tree, multiple sequence alignment, ClustalW, MUSCLE, IQ-TREE, neighbor-joining, newick, protein evolution]
produces: [alignment_file, newick_tree_file, tree_image_png]
domain: genetics
source: biomni:tool/genetics.py::analyze_protein_phylogeny
---
# Analyze Protein Phylogeny

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept a FASTA file path or FASTA string; if a string, write to a temp file. Count sequences with `Bio.SeqIO.parse`.
2. Multiple sequence alignment (choose one):
   - `clustalw`: run `Bio.Align.Applications.ClustalwCommandline`; fall through to `muscle` on failure.
   - `muscle`: run `Bio.Align.Applications.MuscleCommandline`; on failure fall back to pairwise progressive alignment via `Bio.pairwise2.align.globalxx` and write a minimal CLUSTAL-format file.
   - `pre-aligned`: copy input directly to the alignment file.
3. Tree construction:
   - `iqtree`: run `iqtree -s <alignment> -m LG -bb 1000` via `subprocess.run`; rename `.treefile` output to the standard name.
   - On IQ-TREE failure: fall back to Biopython `DistanceCalculator("identity")` + `DistanceTreeConstructor().nj(dm)` and write Newick via `Bio.Phylo.write`.
4. Visualise the Newick tree with `Bio.Phylo.draw` into a matplotlib figure sized to the number of sequences; save as PNG.

## Key decisions
- LG substitution model with ultrafast bootstrap (`-bb 1000`) is a sensible default for proteins; expose the model as a parameter for domain-specific proteins (e.g. WAG, JTT).
- The fallback neighbor-joining uses identity distance, which is approximate; prefer IQ-TREE for publication-quality trees.
- ClustalW/MUSCLE wrappers require the external binaries to be installed; `clustalo` (Clustal Omega) is the modern replacement for ClustalW.

## Caveats
- `Bio.Align.Applications` wrappers are deprecated in recent Biopython; use `subprocess` calls directly or `pyhmmer`/`muscle` Python APIs.
- For large datasets (>500 sequences), alignment and tree construction can be slow; consider `mafft --auto` and `FastTree` as faster alternatives.
- The pairwise2 fallback produces a rough progressive alignment, not a true MSA.

## In ABA
Implement with `run_python`; `ensure_capability("biopython", "matplotlib")`; ensure `iqtree` and `clustalw`/`muscle` binaries are available in the execution environment. Original impl: `source` -> lift to lakeFS later.
