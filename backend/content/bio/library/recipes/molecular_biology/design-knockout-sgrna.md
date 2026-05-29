---
name: design-knockout-sgrna
description: Design candidate SpCas9 (NGG) knockout sgRNAs for a human/mouse gene de novo — fetch the gene's coding sequence from Ensembl REST, find 20nt protospacers next to an NGG PAM in early coding exons, apply real quality filters, and return ranked candidates.
when_to_use: When designing a CRISPR knockout experiment and you need guide-RNA sequences for a gene, given only the gene symbol. Produces CANDIDATE guides from the real coding sequence; for production work pair with a dedicated on-/off-target scorer (CRISPOR, FlashFry).
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [CRISPR, sgRNA, guide RNA, knockout, KO, Cas9, SpCas9, NGG, PAM, protospacer, gene editing, Ensembl, CDS, exon, human, mouse]
produces: [ranked candidate sgRNA sequences with PAM, strand, GC%, and coding position]
domain: molecular_biology
source: "Ensembl REST API (rest.ensembl.org) + SpCas9 design heuristics (Doench/CRISPOR guidance)"
---
# Design knockout sgRNAs (de novo from the coding sequence)

Design SpCas9 knockout guides for a gene from its **real coding sequence**:
pull the CDS from Ensembl REST, scan both strands for 20-nt protospacers next
to an `NGG` PAM inside the **early coding exons** (an early frameshift kills the
protein), filter on standard quality rules, and rank a few candidates.

> Honesty: this returns *candidate* guides from sequence + heuristics. It is
> **not** a substitute for a dedicated guide designer. Rigorous on-target
> efficiency scoring (Rule Set 2 / Doench) and genome-wide off-target counting
> need a real tool — **CRISPOR** (web/CLI) or **FlashFry** (local, with a
> genome index). Mention that as the upgrade and don't oversell these guides.

`ensure_capability("requests")`, then `run_python`.

## 1. Resolve the gene and fetch its coding sequence
```python
import requests, re, time
ENS = "https://rest.ensembl.org"
H = {"Content-Type": "application/json", "Accept": "application/json"}
gene, species = "BRCA1", "homo_sapiens"        # species uses underscores

look = requests.get(f"{ENS}/lookup/symbol/{species}/{gene}?expand=1", headers=H).json()
# canonical transcript (fall back to the first if none is flagged)
tr  = next((t for t in look["Transcript"] if t.get("is_canonical") == 1), look["Transcript"][0])
tid = tr["id"]
cds = requests.get(f"{ENS}/sequence/id/{tid}?type=cds", headers=H).json()["seq"].upper()
```
The `type=cds` sequence is the spliced coding sequence (start codon → stop),
5'→3' on the transcribed strand, so anything found inside it is in CODING
sequence by construction. Scanning the CDS and its reverse complement covers
PAMs on both genomic strands within the coding region.

## 2. Scan early coding exons for 20nt protospacers next to an NGG PAM
```python
def revcomp(s):
    c = {"A":"T","T":"A","G":"C","C":"G","N":"N"}
    return "".join(c.get(b, "N") for b in reversed(s))

def gc(s):
    return 100.0 * (s.count("G") + s.count("C")) / len(s)

# Target the early coding region (early frameshift => null allele).
# Use the first ~30% of the CDS, capped to a sane window.
early_len = min(len(cds), max(300, int(0.30 * len(cds))))
region = cds[:early_len]

def find_guides(seq, strand):
    out = []
    for j in range(21, len(seq) - 1):          # need 20nt + N + GG
        if seq[j+1:j+3] == "GG":               # PAM = NGG: N at j, GG at j+1,j+2
            proto, pam = seq[j-20:j], seq[j:j+3]
            if len(proto) == 20 and "N" not in proto:
                out.append({"strand": strand, "guide": proto, "pam": pam,
                            "gc": round(gc(proto), 1), "cds_pos": j - 20})
    return out

cands = find_guides(region, "+") + find_guides(revcomp(region), "-")
```

## 3. Quality filters (real, not cosmetic)
```python
def keep(c):
    if not (40 <= c["gc"] <= 70):        return False   # GC 40-70%: extremes cut poorly
    if "TTTT" in c["guide"]:             return False   # poly-T terminates the U6 promoter
    g = c["guide"]
    if g.count(g[0]) == 20:              return False   # homopolymer / junk
    return True

cands = [c for c in cands if keep(c)]
```
Why these: **GC 40-70%** — very low or very high GC guides cut inefficiently;
**no `TTTT`** — a run of ≥4 T's is a Pol III (U6) terminator and truncates the
sgRNA; avoid homopolymer/low-complexity guides.

## 4. Dedupe, rank, report
```python
seen, uniq = set(), []
for c in cands:
    if c["guide"] in seen:  continue
    seen.add(c["guide"]); uniq.append(c)

# Heuristic ranking: GC closest to ~55% (sweet spot), then earlier in the CDS.
uniq.sort(key=lambda c: (abs(c["gc"] - 55), c["cds_pos"]))

print(f'{gene} {look["id"]}  transcript {tid}  CDS {len(cds)} nt  -> {len(uniq)} candidates')
for c in uniq[:6]:
    print(f'  {c["strand"]} {c["guide"]}  PAM={c["pam"]}  GC={c["gc"]}%  cds_pos={c["cds_pos"]}')
```

## Verified example (human BRCA1)
`gene=BRCA1` → `ENSG00000012048`, canonical transcript `ENST00000357654`,
CDS 5592 nt → **66 candidate guides** after filtering, in ~4 s. Top picks
(all confirmed to occur in the real BRCA1 CDS):

| strand | 20nt protospacer       | PAM | GC%  | CDS pos |
|--------|------------------------|-----|------|---------|
| −      | `GAGGCTTGCCTTCTTCCGAT` | AGG | 55.0 | 264 |
| −      | `TGTTGGCTCCTTGCTAAGCC` | AGG | 55.0 | 724 |
| +      | `GCAAACAGCCTGGCTTAGCA` | AGG | 55.0 | 922 |
| −      | `CTTGACCATTCTGCTCCGTT` | TGG | 50.0 | 44  |

The full sgRNA to clone is the 20-nt protospacer (the PAM is in the genome, NOT
in the guide).

## Caveats
- These are **candidates**, ranked by simple sequence heuristics — NOT scored
  for on-target efficiency or genome-wide off-targets. For an experiment, run
  the candidates (or the gene) through **CRISPOR** or **FlashFry** to get
  specificity/efficiency scores against the genome before ordering.
- Targets the canonical transcript's early CDS; for genes with important
  isoforms, target a constitutive (shared) early exon.
- SpCas9 / `NGG` only. Other nucleases (Cas12a `TTTV`, SaCas9 `NNGRRT`) need a
  different PAM regex.
- Genomic strand: a guide found in the reverse complement of the CDS targets
  the opposite genomic strand — both are valid for cutting.

## In ABA
`ensure_capability("requests")`, run every step in `run_python`. The gene
lookup + sequence retrieval mirror the `query-ensembl` recipe. To upgrade to
scored guides, add CRISPOR/FlashFry as a capability and pass these candidates
(or the locus) to it.
