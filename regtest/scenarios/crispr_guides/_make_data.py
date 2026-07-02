#!/usr/bin/env python
"""Stage the REAL human BRCA1 coding sequence for the crispr_guides scenario.

The science is SpCas9 knockout-guide design, which must be done from the genuine
BRCA1 CDS — fabricated guide sequences are the failure mode under test. To keep
the scenario COMPUTE-bound (not network-bound) we fetch the CDS from Ensembl
exactly ONCE here at build time and commit it as a local FASTA. The agent then
designs guides locally from the staged file; no per-turn network is required.

Source: Ensembl REST, BRCA1 canonical transcript ENST00000357654 (MANE Select),
        type=cds. ~5.5 kb — well under the 2 MB cap.

Deterministic: a single fixed accession, no random numbers. If the committed
FASTA already exists this script is a no-op verifier (so the file stays
byte-stable and the build is reproducible offline once staged).
"""
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# MANE Select / canonical BRCA1 transcript; type=cds gives the in-frame ORF
# (starts ATG, ends TAA/TAG/TGA), which is exactly what guide design needs.
TRANSCRIPT = "ENST00000357654"
URL = (
    f"https://rest.ensembl.org/sequence/id/{TRANSCRIPT}"
    "?type=cds;content-type=text/x-fasta"
)
OUT = os.path.join(DATA, "BRCA1_cds.fasta")


def fetch_cds() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": "aba-scenario"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode()
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    seq = "".join(ln for ln in lines if not ln.startswith(">")).upper()
    if not re.fullmatch(r"[ACGT]+", seq):
        raise RuntimeError("CDS has non-ACGT characters")
    return seq


def design_count(seq: str) -> tuple[int, int]:
    """Self-check helper: count candidate SpCas9 protospacers on the + strand.

    A naive 20 nt protospacer + NGG PAM scan, then how many pass the simple
    quality filter (GC 40-70%, no TTTT poly-T terminator). NOT the agent's job
    — just to confirm the planted truth that plenty of valid guides exist.
    """
    n_raw = n_ok = 0
    for i in range(len(seq) - 22):
        proto = seq[i:i + 20]
        pam = seq[i + 21:i + 23]
        if pam[0] != "" and seq[i + 21] != "N" and seq[i + 22] == "G" and seq[i + 23 - 1] == "G":
            pass
        # PAM = positions [i+20, i+21, i+22] = N G G
        pam3 = seq[i + 20:i + 23]
        if len(pam3) == 3 and pam3[1] == "G" and pam3[2] == "G":
            n_raw += 1
            gc = (proto.count("G") + proto.count("C")) / 20.0
            if 0.40 <= gc <= 0.70 and "TTTT" not in proto:
                n_ok += 1
    return n_raw, n_ok


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(OUT):
        with open(OUT) as fh:
            seq = "".join(ln.strip() for ln in fh if not ln.startswith(">"))
        print(f"exists: {OUT} ({len(seq)} nt) — verifying only")
    else:
        seq = fetch_cds()
        with open(OUT, "w") as fh:
            fh.write(f">{TRANSCRIPT} BRCA1 cds (Ensembl, MANE Select)\n")
            fh.write("\n".join(seq[i:i + 60] for i in range(0, len(seq), 60)))
            fh.write("\n")
        print(f"wrote {OUT}")

    # planted-truth self-check
    assert len(seq) % 3 == 0, "CDS not a multiple of 3"
    assert seq.startswith("ATG"), "CDS does not start with ATG"
    assert seq[-3:] in {"TAA", "TAG", "TGA"}, "CDS does not end on a stop"
    n_raw, n_ok = design_count(seq)
    print(f"  length          = {len(seq)} nt ({len(seq)//3} codons incl. stop)")
    print(f"  + strand NGG protospacers (raw) = {n_raw}")
    print(f"  passing GC 40-70% & no poly-T   = {n_ok}")
    print(f"  size            = {os.path.getsize(OUT)} bytes")
    assert n_ok >= 50, "expected plenty of designable guides"


if __name__ == "__main__":
    main()
