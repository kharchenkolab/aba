---
name: analyze-protein-phylogeny
description: Multiple sequence alignment then phylogenetic tree construction from a set of protein sequences, with a rendered tree image
when_to_use: When asked to infer evolutionary relationships / build a phylogenetic tree from proteins — either given FASTA, OR given a gene + a set of species (fetch the orthologs first via the UniProt step, then align + tree)
requires_tools: [run_python]
capabilities_needed: [biopython, matplotlib]
keywords: [phylogeny, phylogenetic tree, multiple sequence alignment, MAFFT, MUSCLE, FastTree, IQ-TREE, neighbor-joining, newick, protein evolution, ortholog, orthologs, UniProt, fetch protein sequences, cross-species, taxid]
produces: [alignment_file, newick_tree_file, tree_image_png]
domain: genetics
source: "MAFFT/MUSCLE docs + Biopython Bio.Phylo / Bio.AlignIO API (>= 1.84)"
---
# Analyze Protein Phylogeny

Two stages: **align** the proteins (an external aligner CLI — MAFFT or MUSCLE),
then **build a tree** from the alignment. The tree can come from a fast ML tool
(FastTree/IQ-TREE) or, with no extra tool, neighbor-joining in pure Biopython.

> **Do NOT use `Bio.Align.Applications`.** Those command-line wrappers
> (`MuscleCommandline`, `ClustalwCommandline`, …) were **removed in Biopython
> 1.84** — importing them raises `ModuleNotFoundError` immediately. Call the
> aligner via `subprocess` and read its output with `Bio.AlignIO`.

**Provision:** `ensure_capability("biopython")` and `ensure_capability("matplotlib")`
(both pip). For the aligner/tree binaries, find them with `search_bioconda`, then
`propose_capability` + `ensure_capability` (all on the bioconda channel):
- aligner: `mafft` (recommended, robust `--auto`) or `muscle` (v5).
- ML tree (optional): `fasttree` (fast) or `iqtree` (model selection + bootstrap).

## Fetching the sequences (when given a GENE + species, not FASTA)
If the user names a gene and a set of species ("TP53 across human, mouse, rat, …")
rather than handing you FASTA, fetch the canonical (SwissProt-reviewed) protein per
species from **UniProt REST** — this exact query is robust; do NOT guess accessions
or use the old `uniprot.org/uniprot/?query=` endpoint (both 400 / return the wrong
isoform). Resolve each species to its NCBI **taxid** and:
```python
import urllib.request, urllib.parse
taxa = {"human": 9606, "mouse": 10090, "rat": 10116, "zebrafish": 7955, "chicken": 9031}
gene = "TP53"
recs = []
for sp, tax in taxa.items():
    q = f"gene:{gene} AND organism_id:{tax} AND reviewed:true"          # reviewed = SwissProt canonical
    url = "https://rest.uniprot.org/uniprotkb/search?" + urllib.parse.urlencode(
        {"query": q, "format": "fasta", "size": 1})
    fa = urllib.request.urlopen(url, timeout=30).read().decode()
    if fa.strip():
        recs.append(fa.strip())                                         # keep the >sp|…| header
fasta_path = os.path.join(os.environ.get("ABA_WORK_DIR", "."), f"{gene}_orthologs.fasta")
open(fasta_path, "w").write("\n".join(recs) + "\n")
```
(`urllib` is stdlib — no `requests` needed. If a species has no reviewed entry, drop
`AND reviewed:true` and take `size=1`.) Then proceed to Input/alignment below.

## Input
Accept a FASTA path or a FASTA string (write the string to a temp file). Count
the sequences first — a tree needs **>= 3**.
```python
import os, subprocess, tempfile
from Bio import SeqIO, AlignIO, Phylo

fasta_path = os.path.join(os.environ["DATA_DIR"], "proteins.fasta")  # or write a passed string
n = sum(1 for _ in SeqIO.parse(fasta_path, "fasta"))
assert n >= 3, f"need >= 3 sequences to build a tree, got {n}"
work = os.environ.get("ABA_WORK_DIR", tempfile.mkdtemp())
aln_path = os.path.join(work, "alignment.fasta")
```

## Stage 1 — multiple sequence alignment (subprocess, NOT Bio.Align.Applications)
Pick ONE aligner. Both write aligned FASTA that `Bio.AlignIO` reads as `"fasta"`.

**MAFFT** (recommended) — auto-selects strategy; output goes to stdout:
```python
with open(aln_path, "w") as out:
    subprocess.run(["mafft", "--auto", "--anysymbol", fasta_path],
                   stdout=out, check=True)
```
**MUSCLE v5** — note the v5 flags are `-align`/`-output` (the old `-in`/`-out`
were v3 and will error on v5):
```python
subprocess.run(["muscle", "-align", fasta_path, "-output", aln_path], check=True)
# very large input (thousands of seqs): use `-super5` instead of `-align`.
```
Read the result back as an alignment object:
```python
aln = AlignIO.read(aln_path, "fasta")
print("aligned", len(aln), "sequences ×", aln.get_alignment_length(), "columns")
```

## Stage 2 — build the tree

### Option A (default, no extra tool) — neighbor-joining via Bio.Phylo
Pure Biopython; good for a quick tree of a modest set. For **proteins** use a
substitution-aware model such as `"blosum62"` (the generic `"identity"` model is
for nucleotides / rough use). `nj` (or `upgma`) returns a `Bio.Phylo` tree:
```python
from Bio.Phylo.TreeConstruction import DistanceCalculator, DistanceTreeConstructor
dm   = DistanceCalculator("blosum62").get_distance(aln)   # protein distance model
tree = DistanceTreeConstructor().nj(dm)                    # neighbor-joining
tree.ladderize()                                           # tidy ordering for display
nwk = os.path.join(os.environ["ARTIFACTS_DIR"], "tree.nwk")
Phylo.write(tree, nwk, "newick")
```

### Option B (publication-quality) — maximum-likelihood with FastTree / IQ-TREE
Provision the tool first (see Provision above), then run on the alignment:
```python
nwk = os.path.join(os.environ["ARTIFACTS_DIR"], "tree.nwk")
# FastTree (fast ML; -lg = LG protein model):
with open(nwk, "w") as out:
    subprocess.run(["FastTree", "-lg", aln_path], stdout=out, check=True)
# OR IQ-TREE (model selection + 1000 ultrafast bootstraps); writes <prefix>.treefile:
# subprocess.run(["iqtree", "-s", aln_path, "-m", "LG", "-bb", "1000",
#                 "-pre", os.path.join(work, "iqtree")], check=True)
# then read work/iqtree.treefile
tree = Phylo.read(nwk, "newick")
```
(FastTree binary is often `FastTree`; some bioconda builds name it `fasttree` —
check what `ensure_capability` reports.)

## Stage 3 — render the tree image (headless)
```python
import matplotlib
matplotlib.use("Agg")                                      # headless backend
import matplotlib.pyplot as plt
fig = plt.figure(figsize=(8, max(3, 0.4 * len(aln))))      # scale height to leaf count
Phylo.draw(tree, axes=fig.add_subplot(1, 1, 1), do_show=False)
png = os.path.join(os.environ["ARTIFACTS_DIR"], "tree.png")
fig.savefig(png, dpi=150, bbox_inches="tight")
```

## Key decisions
- **Aligner:** MAFFT `--auto` is the robust default; MUSCLE v5 is fine for small
  sets. Use MAFFT for large/divergent inputs.
- **Tree method:** NJ (Option A) is dependency-free and instant — good for a first
  look. For anything reportable, prefer ML (FastTree fast, IQ-TREE with `-bb 1000`
  for bootstrap support). LG is a sensible default protein model (WAG/JTT for some
  families).
- **Protein distance model** for NJ is `"blosum62"`, not `"identity"`.

## Caveats
- `Bio.Align.Applications` is gone in Biopython >= 1.84 — use `subprocess`. This
  recipe targets the installed Biopython (1.87).
- The aligner/tree binaries are external — `ensure_capability` them (bioconda)
  before the `subprocess.run`; a missing binary raises `FileNotFoundError`.
- MUSCLE v5 flags (`-align`/`-output`) differ from v3 (`-in`/`-out`).
- NJ with an identity-style distance is approximate; for large or divergent sets
  prefer ML.
- Render headless (`matplotlib.use("Agg")`) — there is no display in the executor.

## In ABA
Implement with `run_python`. `ensure_capability("biopython")` +
`ensure_capability("matplotlib")`; provision the aligner (and optional ML tree
tool) from bioconda via `search_bioconda` → `propose_capability` →
`ensure_capability`. Write `alignment.fasta` to the work dir and `tree.nwk` /
`tree.png` under `ARTIFACTS_DIR`.
