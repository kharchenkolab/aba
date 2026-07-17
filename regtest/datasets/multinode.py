"""Multi-node live-agent study (misc/detached_compute.md S4; follows datasets2 D5).

REAL agent turns + REAL substrate, no stubs: a detached compute node (the
dockerized weft-slurm fixture, cross-OS from this mac controller) reachable
via run_python/run_r `site=`. Asserts the agent (i) sizes up + uses external
nodes and (ii) chains node → local → node, and that (iii) the status
surfaces the cards render from tell the local/remote truth.

Substrate env (written by the launcher):
  ABA_MN_CLUSTER_PORT / ABA_MN_KEYDIR  — the docker fixture (falls back to
  /tmp/aba_mn_port.txt + /tmp/aba_mn_keydir.txt)

Run:  python regtest/datasets/multinode.py [--only name,name]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study  # noqa: E402 — throwaway home, oauth bridge, portal, drive_turn, harness

from study import (  # noqa: E402
    drive_turn, tools_named, all_text, run_scenario, scenario, RESULTS,
)
from core.graph.entities import find_entities, get_entity  # noqa: E402

R_DATA = "/home/physicist/aba-mn-data/readings"   # ON the hpc fixture


def _cluster_conn():
    port = os.environ.get("ABA_MN_CLUSTER_PORT")
    keydir = os.environ.get("ABA_MN_KEYDIR")
    try:
        port = port or Path("/tmp/aba_mn_port.txt").read_text().strip().replace("port=", "")
        keydir = keydir or Path("/tmp/aba_mn_keydir.txt").read_text().strip()
    except Exception:  # noqa: BLE001
        return None
    return {"port": int(port), "keydir": keydir}


def hssh(cmd: str):
    """Run a command ON the hpc fixture (the detached node)."""
    import subprocess
    conn = _cluster_conn()
    return subprocess.run(
        ["ssh", "-i", f"{conn['keydir']}/id_ed25519",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
         "-p", str(conn["port"]), "physicist@127.0.0.1", cmd],
        capture_output=True, text=True, timeout=120)


def _run_by_title(frag):
    for r in find_entities(type="analysis", not_deleted=True):
        if frag.lower() in (r.get("title") or "").lower():
            return get_entity(r["id"])
    return None


def _durable(client, rid):
    r = client.get(f"/api/runs/{rid}/durable?flat=1")
    return r.json() if r.status_code == 200 else {"files": []}


def _site_ran(caps):
    """The site= a background run_python/run_r actually carried."""
    sites = []
    for t in tools_named(caps, "run_python") + tools_named(caps, "run_r"):
        s = t["input"].get("site")
        if s:
            sites.append(s)
    return sites


# ── scenarios ─────────────────────────────────────────────────────────────────

@scenario("mn_size_up")
def mn_size_up(client, pid, tid):
    """(i) The agent consults describe_compute and chooses the remote node for
    a heavy step whose data lives there — rather than pulling data local."""
    hssh(f"mkdir -p {R_DATA} && head -c 40000000 /dev/urandom > {R_DATA}/block.bin"
        f" && seq 1 800 | awk '{{print $1\",\"($1*7)%13}}' > {R_DATA}/readings.csv")
    caps = [drive_turn(client, pid, tid,
        f"A ~40 MB data collection lives on the machine 'hpc' at {R_DATA} "
        f"(readings.csv + a big binary block). I want a heavy summarization "
        f"step over it. First check what compute is available, then run that "
        f"step wherever makes the most sense — do NOT copy the big data here. "
        f"Compute the row-count and the sum of the second column of "
        f"readings.csv, and save a summary file next to the run.")]
    txt = all_text(caps).lower()
    local_touches_big = any(
        "block.bin" in (t["input"].get("code") or "")
        for t in tools_named(caps, "run_python") if not t["input"].get("site"))
    return caps, [
        ("describe_compute consulted", bool(tools_named(caps, "describe_compute"))),
        ("ran the step ON hpc (site=)", "hpc" in _site_ran(caps)),
        ("no LOCAL code touched the big file", not local_touches_big),
        ("reports where it ran", "hpc" in txt),
    ]


@scenario("mn_hop_chain")
def mn_hop_chain(client, pid, tid):
    """(ii) node → local → node: compute on hpc, keep/bring back, follow up
    LOCALLY on that result, then a SECOND hpc step consuming the local product.
    Ground-truth threaded by a deterministic series."""
    hssh(f"mkdir -p {R_DATA} && seq 1 500 | awk '{{print $1\",\"($1*3)%17}}' > "
        f"{R_DATA}/series.csv")
    total = sum((i * 3) % 17 for i in range(1, 501))
    caps = [drive_turn(client, pid, tid,
        f"Open an analysis run titled 'Hop chain' for this work. The file "
        f"{R_DATA}/series.csv is on machine 'hpc'. STEP 1: on hpc, sum its "
        f"second column and write stage1.txt with just that number. Bring "
        f"stage1.txt back to me here.")]
    caps.append(drive_turn(client, pid, tid,
        "STEP 2 (locally): read stage1.txt, multiply that number by 2, and "
        "write stage2.txt with the result. Tell me the number."))
    caps.append(drive_turn(client, pid, tid,
        "STEP 3: back on hpc, read the stage-2 value and add 1000 to it. "
        "Report the final number and confirm which machine each step ran on."))
    # step 1 + step 3 are remote; step 2 is local (no site)
    s1_remote = "hpc" in _site_ran([caps[0]])
    s3_remote = "hpc" in _site_ran([caps[2]])
    s2_local = not _site_ran([caps[1]])
    txt = all_text(caps)
    return caps, [
        ("step 1 ran on hpc", s1_remote),
        ("step 2 ran locally", s2_local),
        ("step 3 ran on hpc again", s3_remote),
        ("stage-1 total is correct", str(total) in txt),
        ("stage-2 (x2) is correct", str(total * 2) in txt),
        ("final (+1000) is correct", str(total * 2 + 1000) in txt),
    ]


@scenario("mn_status_surfaces")
def mn_status_surfaces(client, pid, tid):
    """(iii) After a remote run, the SURFACES the cards render from tell the
    truth: the run's durable view marks remote files, the ledger sees the
    site, then bring-back makes them local — asserted on the exact JSON."""
    hssh(f"mkdir -p {R_DATA} && seq 1 50 > {R_DATA}/seed.txt")
    caps = [drive_turn(client, pid, tid,
        f"Open an analysis run titled 'Remote production'. Then, on machine "
        f"'hpc', read {R_DATA}/seed.txt and write two files in the run: "
        f"series.txt (the numbers doubled) and marker.txt (containing "
        f"'done'). Keep them safe on hpc — don't move them off yet.")]
    ent = _run_by_title("Remote production")
    rid = ent["id"] if ent else None
    if not rid:   # fallback: any run whose durable view saw the outputs
        for r in find_entities(type="analysis", not_deleted=True):
            dv = _durable(client, r["id"])
            if any(f["rel"].endswith(("series.txt", "marker.txt")) for f in dv["files"]):
                rid = r["id"]
                break
    checks = [("a run captured the remote outputs", bool(rid))]
    if rid:
        dv = _durable(client, rid)
        remote = [f for f in dv["files"] if (f.get("site") or "") not in ("", "local")]
        led = client.get(f"/api/projects/{pid}/data-ledger").json()
        checks += [
            ("durable view marks files on hpc", any(
                f.get("site") == "hpc" for f in dv["files"])),
            ("remote-kept badge reads 'kept ✓ · on hpc'", any(
                str(f.get("badge", "")).startswith("kept ✓") and "hpc" in f.get("badge", "")
                for f in remote)),
            ("ledger sees hpc as a remote site",
             "hpc" in (led.get("remote_sites") or [])),
        ]
        bb = client.post(f"/api/runs/{rid}/bring-back")
        time.sleep(4)
        dv2 = _durable(client, rid)
        checks.append(("files servable locally after bring-back", any(
            f.get("url") for f in dv2["files"] if f["rel"].endswith("series.txt"))))
        caps.append(drive_turn(client, pid, tid,
            f"Where do this run's files live now, and are they safe? Brief."))
        atxt = caps[-1]["text"].lower()
        checks.append(("agent names hpc + safety", "hpc" in atxt
                       and any(w in atxt for w in ("safe", "kept", "here", "brought"))))
    return caps, checks


@scenario("mn_honesty")
def mn_honesty(client, pid, tid):
    """(i) A machine that does NOT exist must never be silently faked: asked
    to run on a bogus site, the agent surfaces the real options (or runs where
    it can) — never claims execution on a machine it never reached."""
    caps = [drive_turn(client, pid, tid,
        "Run on the machine 'atlantis' (background): compute the sum of the "
        "cubes of 1..100. If that machine isn't available, tell me what IS, "
        "and run it wherever you can — say where it actually ran.")]
    txt = all_text(caps).lower()
    answer = str(sum(i ** 3 for i in range(1, 101)))
    fake = ("ran on atlantis" in txt or "executed on atlantis" in txt)
    honest = any(w in txt for w in ("atlantis", "not available", "no machine",
                                    "unknown", "isn't", "not a declared",
                                    "locally", "hpc"))
    return caps, [
        ("computed the answer", answer in all_text(caps)),
        ("did not fake execution on the bogus machine", not fake),
        ("acknowledged reality / offered real options", honest),
    ]


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    from core.compute import adapter as ad
    st = ad.configure()
    assert st["ok"], st["detail"]
    c = ad.get_compute()
    conn = _cluster_conn()
    if not conn:
        sys.exit("[mn] no cluster fixture — set ABA_MN_CLUSTER_PORT + "
                 "ABA_MN_KEYDIR (or write /tmp/aba_mn_{port,keydir}.txt)")
    c.sync_call("register_site", "hpc", "slurm",
                {"root": "/home/physicist/.weft", "host": "127.0.0.1",
                 "port": conn["port"], "user": "physicist",
                 "ssh_opts": ["-i", f"{conn['keydir']}/id_ed25519",
                              "-o", "StrictHostKeyChecking=no",
                              "-o", "UserKnownHostsFile=/dev/null",
                              "-o", "IdentitiesOnly=yes"]})
    print("[mn] hpc (docker slurm, detached) registered")

    from fastapi.testclient import TestClient
    from main import app
    scenarios = [(fn._scenario, fn) for fn in
                 [mn_size_up, mn_hop_chain, mn_status_surfaces, mn_honesty]]
    try:
        with TestClient(app) as client:
            try:
                for name, fn in scenarios:
                    if only and name not in only:
                        continue
                    run_scenario(client, name, fn)
            finally:
                try:
                    ad.get_compute().sync_call("site_unregister", "hpc")
                except Exception:  # noqa: BLE001
                    pass
                print("[cleanup] hpc unregistered")
    finally:
        out = hssh("rm -rf /home/physicist/aba-mn-data && echo cleaned")
        print("[cleanup] hpc data dirs:", out.stdout.strip() or out.stderr[-120:])
    print("\nMULTINODE:", "ALL PASS" if all(ok for _, ok in RESULTS)
          else "FAILURES: " + ", ".join(n for n, ok in RESULTS if not ok))
    sys.exit(0 if all(ok for _, ok in RESULTS) else 1)


if __name__ == "__main__":
    main()
