#!/usr/bin/env python
"""alphafold: pre-stage the AlphaFold DB model for human p53 (UniProt P04637).

The science is COMPUTE-bound, not network-bound: we fetch the AlphaFold model
ONCE here at build time and commit it to data/. The agent then reads the local
PDB and computes everything (the per-residue pLDDT profile, region means,
confidence bands) from those coordinates — no per-turn network.

AlphaFold convention: the per-residue pLDDT confidence is stored in the
B-factor column of every atom (constant within a residue). data/ holds the
real AF model so the agent reads REAL pLDDT, never invents it.

Run:
  "" _make_data.py
"""
import os
import sys
import json
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

ACC = "P04637"  # human cellular tumor antigen p53
API = f"https://alphafold.ebi.ac.uk/api/prediction/{ACC}"


def _get(url, as_json=False, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw.decode()) if as_json else raw


def main() -> int:
    # 1) Resolve the current model file URL via the AF API (version-proof).
    try:
        rec = _get(API, as_json=True)
        rec = rec[0] if isinstance(rec, list) else rec
        pdb_url = rec["pdbUrl"]
        ver = rec.get("latestVersion")
        print(f"AlphaFold entry {rec.get('entryId')} v{ver}, residues "
              f"{rec.get('uniprotStart')}-{rec.get('uniprotEnd')}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL resolving AF API for {ACC}: {e!r}", file=sys.stderr)
        return 1

    fn = os.path.basename(pdb_url)
    dest = os.path.join(DATA, fn)

    # 2) Fetch the PDB once (bounded, ~0.25 MB).
    try:
        data = _get(pdb_url)
        with open(dest, "wb") as f:
            f.write(data)
        print(f"OK wrote {fn} ({len(data)} bytes)")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL fetching {pdb_url}: {e!r}", file=sys.stderr)
        return 1

    # 3) Verify the planted ground truth from the real B-factor (pLDDT) column.
    res_plddt = {}
    with open(dest) as f:
        for line in f:
            if line.startswith("ATOM"):
                res_plddt[int(line[22:26])] = float(line[60:66])
    nums = sorted(res_plddt)
    import statistics as st

    def region_mean(a, b):
        vals = [res_plddt[n] for n in nums if a <= n <= b]
        return round(st.mean(vals), 1)

    print(f"  n residues: {len(nums)}  ({nums[0]}-{nums[-1]})")
    print(f"  DBD (94-312) mean pLDDT:        {region_mean(94, 312)}")
    print(f"  N-term TAD/PRD (1-93) mean:     {region_mean(1, 93)}")
    print(f"  C-term CTD basic (363-393):     {region_mean(363, 393)}")
    print(f"  Tetramerization (323-356):      {region_mean(323, 356)}")
    for r in (175, 248, 273):
        print(f"  hotspot R{r} pLDDT:             {res_plddt.get(r)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
