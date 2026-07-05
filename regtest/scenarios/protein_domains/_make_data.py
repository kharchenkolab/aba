#!/usr/bin/env python
"""Fetch (ONCE, at build time) everything this scenario needs so the SESSION runs LOCALLY.

NETWORK BUDGET: this scenario must be compute/reasoning-bound, never network-bound.
The old design made the agent block on a LIVE InterProScan scan (minutes/sequence).
Instead we pre-stage UniProt's PRECOMPUTED feature record here, so during the test the
agent annotates the protein from a local file it can read in milliseconds — no live scan,
no per-variant REST. Re-running this script just re-downloads fixed records and overwrites
with byte-identical output; nothing here is random.

Outputs (all committed under data/, all small):
  protein.fasta              -> human EGFR isoform 3 (UniProt P00533-3, 705 aa).
                                NON-canonical. Identical to canonical for residues 1-627
                                (the whole extracellular region), then diverges into a unique
                                78-residue C-terminal tail. LACKS the transmembrane helix and
                                the entire intracellular protein-kinase domain.
  protein_canonical.fasta    -> human EGFR canonical sequence (UniProt P00533-1, 1210 aa):
                                signal peptide 1-24, extracellular L/furin domains, single
                                transmembrane helix 646-668, cytoplasmic protein-kinase domain
                                712-979, C-terminal tail.
  uniprot_P00533_features.json -> a SMALL curated slice of UniProt's PRECOMPUTED feature
                                record for P00533: the architecture features (signal / chain /
                                topological domains / transmembrane / kinase domain / regions /
                                repeats / active + binding sites / disulfides / glycosylation
                                counts) AND the catalogue of natural / disease variants (each
                                flagged for kinase-domain membership). This is what a scientist
                                who already pulled the UniProt entry would have on disk. It lets
                                the whole session (architecture + isoform comparison + disease-
                                mutation -> domain mapping) be answered LOCALLY.

Run:
    "" _make_data.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

UA = {"User-Agent": "aba-scenario-protein_domains/1.0 (regression test data)"}

# UniProt accession -> (output filename, anonymized FASTA header).
# We keep the REAL sequences byte-for-byte but strip the UniProt header so the
# agent has to IDENTIFY the protein from the sequence (the point of step 1) rather
# than just read "EGFR" off the defline.
TARGETS = {
    "P00533-3": ("protein.fasta", "query_isoform"),            # non-canonical isoform 3 (the "wrong" one in s1)
    "P00533-1": ("protein_canonical.fasta", "query_canonical"),  # canonical isoform 1 (corrected one in s4)
}

# Canonical kinase-domain bounds (UniProt feature coords) — used to flag variants.
KINASE_START, KINASE_END = 712, 979

# Feature types we keep for the local architecture record. We deliberately DROP the
# bulky per-residue secondary-structure features (Beta strand / Helix / Turn — ~150 of
# them) and the references; those are not needed to read the domain architecture and
# would bloat the file. We KEEP the architecture skeleton + the variant catalogue.
ARCH_TYPES = {
    "Signal", "Chain", "Topological domain", "Transmembrane", "Domain",
    "Region", "Repeat", "Active site", "Binding site", "Disulfide bond",
}


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def fetch_seq(accession: str) -> str:
    url = f"https://rest.uniprot.org/uniprotkb/{accession}.fasta"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("ascii")
    if not text.startswith(">"):
        raise RuntimeError(f"unexpected response for {accession}: {text[:80]!r}")
    return "".join(line for line in text.splitlines() if not line.startswith(">"))


def write_fasta(path: Path, header: str, seq: str) -> None:
    lines = [f">{header}"]
    lines += [seq[i:i + 60] for i in range(0, len(seq), 60)]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _loc(feat: dict) -> tuple[int | None, int | None]:
    s = feat.get("location", {}).get("start", {}).get("value")
    e = feat.get("location", {}).get("end", {}).get("value")
    return s, e


def build_features_record(raw: dict) -> dict:
    """Distil UniProt's full JSON down to a small, self-contained architecture +
    variant record. Coordinates are 1-based UniProt feature coordinates."""
    gene = raw["genes"][0]["geneName"]["value"]
    protein = raw["proteinDescription"]["recommendedName"]["fullName"]["value"]

    architecture: list[dict] = []
    glyco = 0
    for f in raw["features"]:
        t = f["type"]
        if t == "Glycosylation":
            glyco += 1
            continue
        if t not in ARCH_TYPES:
            continue
        s, e = _loc(f)
        entry = {"type": t, "start": s, "end": e}
        desc = f.get("description")
        if desc:
            entry["description"] = desc
        architecture.append(entry)

    variants: list[dict] = []
    for f in raw["features"]:
        if f["type"] != "Natural variant":
            continue
        s, e = _loc(f)
        alt = f.get("alternativeSequence", {}) or {}
        orig = alt.get("originalSequence", "")
        alts = alt.get("alternativeSequences", []) or []
        new = ",".join(alts)
        # Compact HGVS-ish label, e.g. L858R or E746_A750del.
        if s == e and orig and new:
            label = f"{orig}{s}{new}"
        elif s is not None and not new:  # deletion
            label = f"{orig}{s}_{e}del" if e and e != s else f"{orig}{s}del"
        else:
            label = f"{orig}{s}-{e}{('>' + new) if new else ''}"
        in_kinase = bool(s is not None and e is not None
                         and s >= KINASE_START and e <= KINASE_END)
        variants.append({
            "label": label,
            "start": s,
            "end": e,
            "original": orig,
            "variant": new,
            "featureId": f.get("featureId", ""),
            "description": f.get("description", ""),
            "in_kinase_domain": in_kinase,
        })

    diseases = []
    for c in raw.get("comments", []):
        if c.get("commentType") == "DISEASE":
            dis = c.get("disease", {})
            diseases.append({
                "name": dis.get("diseaseId"),
                "acronym": dis.get("acronym"),
                "description": dis.get("description", ""),
            })

    return {
        "_note": (
            "Precomputed UniProt feature record for this protein, pulled once and saved "
            "locally so the domain architecture and disease-variant analysis run without "
            "any live sequence scan. Coordinates are 1-based on the canonical sequence."
        ),
        "accession": raw["primaryAccession"],
        "entry_name": raw["uniProtkbId"],
        "gene": gene,
        "protein_name": protein,
        "organism": raw["organism"]["scientificName"],
        "canonical_length": raw["sequence"]["length"],
        "kinase_domain": {"start": KINASE_START, "end": KINASE_END},
        "n_glycosylation_sites": glyco,
        "architecture": architecture,
        "diseases": diseases,
        "natural_variants": variants,
    }


def main() -> int:
    # --- sequences -------------------------------------------------------------
    seqs = {}
    for acc, (fname, header) in TARGETS.items():
        seq = fetch_seq(acc)
        out = DATA / fname
        write_fasta(out, header, seq)
        seqs[acc] = seq
        print(f"wrote {out}  ({out.stat().st_size} bytes, {len(seq)} aa)")

    # --- precomputed feature record (the network-budget fix) -------------------
    raw = _fetch_json("https://rest.uniprot.org/uniprotkb/P00533.json")
    record = build_features_record(raw)
    feat_path = DATA / "uniprot_P00533_features.json"
    feat_path.write_text(json.dumps(record, indent=2) + "\n", encoding="ascii")
    print(f"wrote {feat_path}  ({feat_path.stat().st_size} bytes, "
          f"{len(record['architecture'])} architecture feats, "
          f"{len(record['natural_variants'])} variants)")

    # --- planted-truth self-checks (a silent UniProt change can't slip through) -
    can, iso = seqs["P00533-1"], seqs["P00533-3"]
    assert len(can) == 1210, f"canonical length changed: {len(can)}"
    assert len(iso) == 705, f"isoform length changed: {len(iso)}"
    div = next(i for i in range(min(len(can), len(iso))) if can[i] != iso[i])
    assert div == 627, f"divergence moved: identical for 1-{div}, expected 1-627"
    assert len(iso) < 712, "isoform unexpectedly reaches the kinase domain start"
    assert iso[645:668] != can[645:668], "isoform tail unexpectedly matches the TM helix"

    arch = {(a["type"], a["start"], a["end"]) for a in record["architecture"]}
    assert ("Signal", 1, 24) in arch, "signal peptide 1-24 missing from feature record"
    assert ("Transmembrane", 646, 668) in arch, "TM helix 646-668 missing"
    assert ("Domain", 712, 979) in arch, "kinase domain 712-979 missing"
    assert ("Topological domain", 25, 645) in arch, "extracellular topo-domain missing"

    kin_vars = [v for v in record["natural_variants"] if v["in_kinase_domain"]]
    assert len(kin_vars) >= 20, f"too few kinase-domain variants: {len(kin_vars)}"
    labels = {v["label"] for v in record["natural_variants"]}
    for must in ("L858R", "T790M", "G719S"):
        assert must in labels, f"expected hallmark variant {must} missing"
    print(f"planted-truth self-check OK: shared 1-627, isoform unique tail 628-705 "
          f"(no TM / no kinase); feature record has signal 1-24, TM 646-668, "
          f"kinase 712-979; {len(kin_vars)} kinase-domain variants incl. L858R/T790M/G719S.")

    total = sum(p.stat().st_size for p in DATA.iterdir() if p.is_file())
    print(f"total data bytes: {total}")
    assert total < 2_000_000, f"data exceeds 2MB budget: {total}"
    return 0


if __name__ == "__main__":
    sys.exit(main())
