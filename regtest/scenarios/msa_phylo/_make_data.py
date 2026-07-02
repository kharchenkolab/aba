#!/usr/bin/env python
"""Build orthologs.fasta for the msa_phylo scenario.

Fetches REAL canonical (SwissProt-reviewed) protein sequences from UniProt:
  * 8 true orthologs of cytochrome c (somatic), one per species
  * 1 deliberate OUTLIER: human cytochrome b5 (a different heme protein,
    NOT a cytochrome c ortholog) — written under the neutral id `seq_outlier`
    so its identity is not handed to the agent.

Deterministic: the species order is fixed and the sequences are pinned to
specific UniProt accessions, so the output file is byte-stable across runs.
No random numbers are used (seed=0 is irrelevant here but set for convention).
"""
import os
import random
import urllib.request

random.seed(0)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# --- True cytochrome c orthologs (accession -> friendly species label) -------
# Labels are corrected to match each accession's real OS= organism.
ORTHOLOGS = [
    ("P99999", "human",          "Homo_sapiens"),
    ("P62897", "mouse",          "Mus_musculus"),
    ("P62898", "rat",            "Rattus_norvegicus"),
    ("P62894", "bovine",         "Bos_taurus"),
    ("P62895", "pig",            "Sus_scrofa"),
    ("P00004", "horse",          "Equus_caballus"),
    ("P00012", "elephant_seal",  "Mirounga_leonina"),
    ("P00025", "tuna",           "Katsuwonus_pelamis"),
]

# --- The planted outlier: human cytochrome b5 (CYB5A, P00167) ----------------
# A genuine heme-binding protein but an entirely different family from
# cytochrome c: no sequence homology, lacks the CXXCH heme-attachment motif,
# and is longer (134 aa). It will degrade the MSA and fall on a long branch.
OUTLIER_ACC = "P00167"


def fetch_fasta(acc: str) -> tuple[str, str]:
    """Return (header_line, sequence) for a UniProt accession."""
    url = f"https://rest.uniprot.org/uniprotkb/{acc}.fasta"
    raw = urllib.request.urlopen(url, timeout=60).read().decode()
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    header = lines[0].lstrip(">")
    seq = "".join(lines[1:])
    if not seq:
        raise RuntimeError(f"empty sequence for {acc}")
    return header, seq


def wrap(seq: str, width: int = 60) -> str:
    return "\n".join(seq[i:i + width] for i in range(0, len(seq), width))


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    records = []

    # Orthologs: keep an informative, species-labeled header (a biologist who
    # assembled an ortholog set would have these). Id = species so trees/labels
    # read cleanly; the original UniProt description is kept as a comment.
    for acc, species, organism in ORTHOLOGS:
        uni_hdr, seq = fetch_fasta(acc)
        rec_id = f"{species}|{acc}"
        header = f">{rec_id} {organism} cytochrome_c [UniProt:{acc}]"
        records.append((rec_id, header, seq))

    # Outlier: NEUTRAL id `seq_outlier`, no species/family hint in the header.
    _, out_seq = fetch_fasta(OUTLIER_ACC)
    records.append(("seq_outlier",
                    f">seq_outlier unknown_provenance [UniProt:{OUTLIER_ACC}]",
                    out_seq))

    out_path = os.path.join(DATA, "orthologs.fasta")
    with open(out_path, "w") as fh:
        for _id, header, seq in records:
            fh.write(header + "\n")
            fh.write(wrap(seq) + "\n")

    # --- report + self-check the planted truth -------------------------------
    def has_cxxch(s: str) -> bool:
        return any(s[i] == "C" and s[i + 3] == "C" and s[i + 4] == "H"
                   for i in range(len(s) - 4))

    print(f"wrote {out_path}")
    print(f"  {len(records)} sequences "
          f"({len(records) - 1} orthologs + 1 outlier)")
    for _id, _hdr, seq in records:
        print(f"    {_id:24s} len={len(seq):3d}  CXXCH={has_cxxch(seq)}")
    n_motif = sum(1 for _i, _h, s in records[:-1] if has_cxxch(s))
    assert n_motif == len(ORTHOLOGS), "all orthologs must carry CXXCH"
    assert not has_cxxch(records[-1][2]), "outlier must LACK CXXCH"
    print(f"  size = {os.path.getsize(out_path)} bytes")


if __name__ == "__main__":
    main()
