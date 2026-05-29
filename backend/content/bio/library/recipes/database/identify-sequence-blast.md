---
name: identify-sequence-blast
description: Identify an unknown DNA/protein sequence (e.g. a mystery FASTA) by similarity search — submit BLAST via the EBI JDispatcher REST API and report the best-matching protein and its organism with alignment stats.
when_to_use: "When you have a sequence and must answer 'what protein/gene is this and what organism is it from?' — an unannotated FASTA, a contig, a peptide. This is a SIMILARITY search, NOT a keyword/accession lookup: do not use UniProt/NCBI text search for this (their search endpoints have no 'sequence' field and will 400)."
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [BLAST, blastp, blastn, sequence similarity, identify sequence, unknown protein, what protein is this, what organism, mystery fasta, homology, e-value, percent identity, EBI, JDispatcher, ncbiblast, swissprot, uniprot]
produces: [best hit accession, protein name, organism, percent identity, E-value, alignment summary]
domain: database
source: "EBI Job Dispatcher REST API — ebi.ac.uk/jdispatcher/docs/webservices (NCBI BLAST+ service)"
---
# Identify a sequence by similarity (BLAST)

You have a sequence and need to know **what it is** and **what organism it
comes from**. That is a *similarity* search. It is NOT a keyword or accession
lookup — UniProt/NCBI text-search endpoints have no `sequence` field and will
reject a raw sequence with **HTTP 400**. Do not flail with `query-uniprot` /
search tools here. BLAST the sequence.

**Primary method: the EBI JDispatcher NCBI BLAST REST API** (`requests` only —
no extra capability, no local database). It is asynchronous (submit → poll →
fetch) but reliably finishes in well under a minute against Swiss-Prot, and its
JSON gives the organism directly (`hit_uni_os`). This is more robust for an
agent than `Bio.Blast.NCBIWWW.qblast` (slower, less structured) — keep qblast
only as a documented fallback (see end).

`ensure_capability("requests")`, then `run_python`.

## 1. Read the sequence + detect protein vs nucleotide
```python
import re
# read the FASTA (single record); strip header + whitespace
raw = open(fasta_path).read()
seq = "".join(l.strip() for l in raw.splitlines() if not l.startswith(">")).upper()
seq = re.sub(r"[^A-Z]", "", seq)

# Nucleotide if it's essentially only ACGTUN; otherwise treat as protein.
nuc = set("ACGTUN")
is_nuc = seq and sum(c in nuc for c in seq) / len(seq) > 0.95
stype, program = ("dna", "blastn") if is_nuc else ("protein", "blastp")
```

## 2. Submit, poll, fetch (EBI REST)
```python
import time, requests
BASE = "https://www.ebi.ac.uk/Tools/services/rest/ncbiblast"

# Swiss-Prot for proteins = clean, reviewed names + organisms; small + fast.
# For nucleotide use a nucleotide db (e.g. "em_rel" EMBL, or "uniprotkb" via blastx
# if you want protein-level identification of a coding sequence).
db = "uniprotkb_swissprot" if stype == "protein" else "em_rel"

run = requests.post(BASE + "/run", data={
    "email": "aba-agent@example.org",   # EBI requires a contact email
    "program": program,
    "stype": stype,
    "database": db,
    "sequence": seq,
    "alignments": "10",                 # keep hitlist small — faster, polite
    "scores": "10",
})
run.raise_for_status()
job_id = run.text.strip()

# Poll. Web BLAST is slow + rate-limited: expect ~20-60 s; cap the wait.
deadline = time.time() + 300
status = "RUNNING"
while time.time() < deadline:
    status = requests.get(f"{BASE}/status/{job_id}").text.strip()
    if status in ("FINISHED", "ERROR", "FAILURE", "NOT_FOUND"):
        break
    time.sleep(5)
assert status == "FINISHED", f"BLAST job ended as {status}"
```

## 3. Parse the top hits + report
```python
res = requests.get(f"{BASE}/result/{job_id}/json").json()
hits = res.get("hits", [])
for h in hits[:5]:
    hsp = h["hit_hsps"][0]                       # best HSP for this hit
    print(f'{h["hit_db"]}:{h["hit_acc"]}  {h["hit_desc"][:70]}')
    print(f'   organism={h.get("hit_uni_os","?")}  %id={hsp["hsp_identity"]}'
          f'  E={hsp["hsp_expect"]}  bits={hsp["hsp_bit_score"]}')

best = hits[0]; bhsp = best["hit_hsps"][0]
print(f'\nIDENTIFIED: {best["hit_desc"]}  ({best.get("hit_uni_os")})  '
      f'acc={best["hit_acc"]}  %id={bhsp["hsp_identity"]}  E={bhsp["hsp_expect"]}')
```
The organism is `hit_uni_os` (UniProt OS field) and is also embedded in the
description as `OS=...`. The gene is in `GN=...`.

## How to read the result
- **High %identity (≳90) + tiny E-value (≤1e-20)** over most of the query →
  a confident identification; report the protein name + organism.
- A perfect-identity hit to one species plus near-perfect hits to its close
  relatives (orthologs) is the expected, healthy signature — name the
  top species but note the homolog spread.
- **Low identity / partial coverage / E-value ≳ 1e-3** → only a distant
  homolog; report it as "similar to …", not "is …".

## Verified example
Human proinsulin
(`MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN`)
→ in ~32 s the top hit is `SP:P01308  Insulin OS=Homo sapiens OX=9606 GN=INS`,
**100% identity, E=1.8e-79**, followed by gorilla/orangutan/chimp insulin
orthologs at 98-99%. Correct call: **human insulin (INS, P01308)**.

## Caveats
- **Web BLAST is slow and rate-limited.** Keep `alphabet`/`alignments` small
  (≤10), submit ONE job at a time with delays, and expect ~20-60 s per search
  (occasionally a minute+ when EBI is busy). Always cap the poll loop.
- EBI requires a contact `email` in the POST.
- Swiss-Prot gives the cleanest names/organisms but is small; if nothing good
  comes back, retry against `uniprotkb` (adds TrEMBL) or NCBI `nr`.
- Short / low-complexity sequences may return no significant hit.

## Fallback: Bio.Blast.NCBIWWW.qblast
If EBI is unavailable, `ensure_capability("biopython")` and use NCBI directly —
slower (~60 s+ here) and the organism must be parsed out of the bracketed
`[Homo sapiens]` in `hit_def`:
```python
from Bio.Blast import NCBIWWW, NCBIXML
h = NCBIWWW.qblast("blastp", "nr", seq, hitlist_size=5, expect=1e-5)
rec = NCBIXML.read(h)
for al in rec.alignments[:5]:
    hsp = al.hsps[0]
    print(al.hit_def[:80], al.accession,
          f"%id={100*hsp.identities/hsp.align_length:.1f}", f"E={hsp.expect:.1e}")
```

## Heavy alternative (usually NOT for this sandbox)
A local search with **diamond** or **mmseqs2** (bioconda) is fast and
offline-capable, but it needs a downloaded reference DB (Swiss-Prot is ~½ GB
amino-acid FASTA; nr is huge). Only worth it for batch/repeated identification
where you can stage a DB — not for a one-off "what is this sequence?".

## In ABA
`ensure_capability("requests")`, run every step in `run_python`. For nucleotide
"what gene/region is this?" against a genome, prefer `query-ensembl` /
`query-ucsc` BLAT when you already know the species; use BLAST here when the
species/identity is unknown.
