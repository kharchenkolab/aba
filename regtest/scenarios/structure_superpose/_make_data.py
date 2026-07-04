#!/usr/bin/env python
"""structure_superpose: no local data is generated.

All coordinates are REAL PDB entries fetched at run time by the agent. This
script commits nothing to data/; it only VERIFIES (deterministically, seed-free
because it is pure lookup) that every PDB ID the scenario relies on still
resolves over HTTP and that the supersession the version_change step depends on
is still the live state of the PDB. Run it to confirm the scenario is wired to
reality before using it.

Run:
  /home/pkharchenko/aba/tools/scenario-venv/bin/python _make_data.py
"""
import os
import sys
import json
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

# IDs the scenario references
APO_OLD = "1P38"   # obsolete apo p38-alpha (DFG-in), superseded
APO_NEW = "5UOJ"   # re-refinement that supersedes 1P38 (version_change target)
COMPLEX = "1KV2"   # p38-alpha + BIRB-796 (DFG-out); ligand HET code B96
LIGAND = "B96"


def _get(url, as_json=False, timeout=60):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw.decode()) if as_json else raw)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def check_downloadable(pid):
    """The coordinate file must be retrievable from the standard RCSB download URL."""
    status, raw = _get(f"https://files.rcsb.org/download/{pid}.pdb")
    ok = status == 200 and raw and raw[:6] == b"HEADER"
    print(f"  download {pid}.pdb : status={status} bytes={len(raw) if raw else 0} "
          f"{'OK' if ok else 'FAIL'}")
    return ok, raw


def check_supersession():
    """1P38 must still be obsolete and replaced_by 5UOJ."""
    status, d = _get(
        f"https://data.rcsb.org/rest/v1/holdings/removed/{APO_OLD}", as_json=True
    )
    repl = []
    if status == 200 and d:
        repl = d.get("rcsb_repository_holdings_removed", {}).get(
            "id_codes_replaced_by", []
        )
    ok = status == 200 and APO_NEW in repl
    print(f"  supersession {APO_OLD} -> replaced_by {repl} "
          f"(expected {APO_NEW!r}) {'OK' if ok else 'FAIL'}")
    return ok


def check_ligand_present(raw):
    """BIRB-796 (HET code B96) must be present in the complex coordinates."""
    if not raw:
        print(f"  ligand {LIGAND} in {COMPLEX}: FAIL (no coordinates)")
        return False
    n = sum(
        1
        for line in raw.decode("latin-1").splitlines()
        if line[:6] == "HETATM" and line[17:20].strip() == LIGAND
    )
    ok = n > 0
    print(f"  ligand {LIGAND} in {COMPLEX}: {n} atoms {'OK' if ok else 'FAIL'}")
    return ok


def main():
    print("structure_superpose: verifying real PDB references resolve (no data committed)")
    ok = True
    ok &= check_downloadable(APO_OLD)[0]
    ok &= check_downloadable(APO_NEW)[0]
    complex_ok, complex_raw = check_downloadable(COMPLEX)
    ok &= complex_ok
    ok &= check_supersession()
    ok &= check_ligand_present(complex_raw)

    n_data = len([f for f in os.listdir(DATA) if not f.startswith(".")])
    print(f"data/ contains {n_data} committed file(s) (expected 0; fetched at run time)")
    if ok:
        print("ALL CHECKS PASSED")
        return 0
    print("ONE OR MORE CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
