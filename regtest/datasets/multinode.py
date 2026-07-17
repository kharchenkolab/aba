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


def wait_jobs_settled(client, pid, timeout_s=300):
    """Block until the project has no queued/running jobs (deferred
    continuations fire after terminal states) — the harness must not assert
    on surfaces before the work it drove has actually finished."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = client.get(f"/api/jobs?project_id={pid}")
        if r.status_code == 200:
            rows = r.json() if isinstance(r.json(), list) else r.json().get("jobs", [])
            if not any((j.get("status") in ("queued", "running")) for j in rows):
                return True
        time.sleep(5)
    return False


def thread_text(client, pid, tid, settle_s=120):
    """ALL text on the thread — including deferred-continuation turns that land
    AFTER a drive_turn's stream ends (a background job's result arrives as a
    new assistant message). Waits for the project's jobs to settle first."""
    import json as _json
    wait_jobs_settled(client, pid, timeout_s=settle_s)
    time.sleep(8)          # continuation turn writes after the job flips
    r = client.get(f"/api/messages?thread_id={tid}&project_id={pid}")
    if r.status_code != 200:
        return ""
    parts = []
    for m in r.json():
        c = m.get("content") or m.get("text") or ""
        parts.append(c if isinstance(c, str) else _json.dumps(c))
    return "\n".join(parts)


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
        f" && (echo idx,val; seq 1 800 | awk '{{print $1\",\"($1*7)%13}}') > {R_DATA}/readings.csv")
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
    # header row makes the parse unambiguous — a headerless file cost run 4:
    # pandas (reasonably) ate row 1 as the header and summed 499 rows
    hssh(f"mkdir -p {R_DATA} && (echo idx,val; seq 1 500 | "
         f"awk '{{print $1\",\"($1*3)%17}}') > {R_DATA}/series.csv")
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
    txt = all_text(caps) + "\n" + thread_text(client, pid, tid)
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
    truth. In-place remote keeps apply to LARGE outputs that STAY on the node
    (small ones auto-come-home — that's the sync/harvest design). So we
    produce a big file on hpc, keep it in place, and assert the durable view /
    ledger / bring-back reflect 'on hpc' — on the exact JSON the cards read."""
    caps = [drive_turn(client, pid, tid,
        "Open an analysis run titled 'Remote production'. Then run a BACKGROUND "
        "job on machine 'hpc' that writes a LARGE ~60 MB file called big.bin "
        "in the run's working directory (e.g. 60*1024*1024 bytes), plus a "
        "small marker.txt containing 'done'. It's big, so KEEP it safe on "
        "hpc in place — do not move it off yet.")]
    wait_jobs_settled(client, pid)
    ent = _run_by_title("Remote production")
    rid = ent["id"] if ent else None
    if not rid:   # fallback: any run whose durable view saw the big output
        for r in find_entities(type="analysis", not_deleted=True):
            dv = _durable(client, r["id"])
            if any(f["rel"].endswith("big.bin") for f in dv["files"]):
                rid = r["id"]
                break
    if rid:       # keeps settle asynchronously (retain at close / keep_outputs)
        t0 = time.time()
        while time.time() - t0 < 180:
            dv = _durable(client, rid)
            if any(f.get("state") == "retained" and f.get("site") == "hpc"
                   for f in dv["files"]):
                break
            time.sleep(6)
    checks = [("a run captured the remote outputs", bool(rid))]
    if rid:
        dv = _durable(client, rid)
        remote = [f for f in dv["files"] if (f.get("site") or "") not in ("", "local")]
        led = client.get(f"/api/projects/{pid}/data-ledger").json()
        checks += [
            ("durable view marks the big file on hpc", any(
                f.get("site") == "hpc" for f in dv["files"])),
            ("remote-kept badge reads 'kept ✓ · on hpc'", any(
                str(f.get("badge", "")).startswith("kept ✓") and "hpc" in f.get("badge", "")
                for f in remote)),
            ("ledger sees hpc as a remote site",
             "hpc" in (led.get("remote_sites") or [])),
        ]
        bb = client.post(f"/api/runs/{rid}/bring-back")
        time.sleep(8)
        dv2 = _durable(client, rid)
        checks.append(("big file servable locally after bring-back", any(
            f.get("url") for f in dv2["files"] if f["rel"].endswith("big.bin"))))
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
    full = all_text(caps) + "\n" + thread_text(client, pid, tid)
    txt = full.lower()
    answer = str(sum(i ** 3 for i in range(1, 101)))
    fake = ("ran on atlantis" in txt or "executed on atlantis" in txt)
    honest = any(w in txt for w in ("atlantis", "not available", "no machine",
                                    "unknown", "isn't", "not a declared",
                                    "locally", "hpc"))
    return caps, [
        ("computed the answer", answer in full),
        ("did not fake execution on the bogus machine", not fake),
        ("acknowledged reality / offered real options", honest),
    ]




@scenario("mn_isolated_env_remote")
def mn_isolated_env_remote(client, pid, tid):
    """Named-env path live: the agent creates an isolated env for a package
    and runs it ON the node — env re-locks for the site platform, realizes
    there, and the import works remotely."""
    caps = [drive_turn(client, pid, tid,
        "Create an isolated environment named 'numtools' containing the "
        "python package 'click'. Then run a background step ON machine "
        "'hpc' inside that env: import click, print its version, and write "
        "ok.txt containing that version. Report the version back to me.")]
    full = all_text(caps) + "\n" + thread_text(client, pid, tid)
    mk = tools_named(caps, "make_isolated_env")
    runs = [t for t in tools_named(caps, "run_python")
            if t["input"].get("site") == "hpc"]
    return caps, [
        ("isolated env created", bool(mk)),
        ("remote job ran IN that env", any(
            (t["input"].get("env") or "") == "numtools" for t in runs)),
        ("a version was reported", any(ch.isdigit() for ch in full)
         and "click" in full.lower()),
    ]


@scenario("mn_crash_fix_rerun")
def mn_crash_fix_rerun(client, pid, tid):
    """The daily-life loop over the detached transport: the remote job fails
    (wrong filename), the agent reads the error, debugs, and lands the
    correct number — never fabricating."""
    hssh(f"mkdir -p {R_DATA} && (echo idx,val; seq 1 60 | "
         f"awk '{{print $1\",\"($1*5)%11}}') > {R_DATA}/vals_v2.csv")
    total = sum((i * 5) % 11 for i in range(1, 61))
    caps = [drive_turn(client, pid, tid,
        f"On machine 'hpc', read the csv in {R_DATA} — I think it's called "
        f"vals.csv — and report the sum of its val column. If something "
        f"fails, debug it right there and get me the number. This is quick, "
        f"so just run it directly (not as a background job).")]
    full = all_text(caps) + "\n" + thread_text(client, pid, tid)
    hpc_runs = [t for t in tools_named(caps, "run_python")
                if t["input"].get("site") == "hpc"]
    return caps, [
        ("worked on the node", bool(hpc_runs)),
        ("found the real file", "vals_v2" in full),
        ("correct sum reported", str(total) in full),
    ]


@scenario("mn_fanout_gather")
def mn_fanout_gather(client, pid, tid):
    """Concurrent detached jobs + continuation ordering: three independent
    variants in parallel (node + local mix), then a local gather step."""
    caps = [drive_turn(client, pid, tid,
        "Run three INDEPENDENT background jobs, in parallel (submit all "
        "before waiting): each computes the sum of i*i for i from 1 to N, "
        "for N = 100, 200 and 300. Run at least one of them on machine "
        "'hpc' and at least one locally. When all three are done, compute "
        "the TOTAL of the three results locally and report it.")]
    full = all_text(caps) + "\n" + thread_text(client, pid, tid)
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("background")]
    sites = [t["input"].get("site") for t in bg]
    expected = {100: 338350, 200: 2686700, 300: 9045050}
    total = sum(expected.values())
    return caps, [
        ("three background jobs submitted", len(bg) >= 3),
        ("at least one on hpc", "hpc" in sites),
        ("at least one local", any(not s for s in sites)),
        ("correct total reported", str(total) in full),
    ]


@scenario("mn_pin_remote_result")
def mn_pin_remote_result(client, pid, tid):
    """Full pipeline: a remote step PRODUCES a plot → harvested back and shown
    in the run's outputs → agent pins it as a Result. Tests the
    harvest-from-remote → pin-to-Results path the platform is built on.
    (Post-cutover, figures become entities only when pinned — so we verify
    the run's OUTPUT MANIFEST carries the plot, then a Result after pinning.)"""
    caps = [drive_turn(client, pid, tid,
        "Open an analysis run titled 'Squares'. Then on machine 'hpc', "
        "generate y = i*i for i in 1..40 and make a line plot saved as "
        "squares.png. Bring it back and show it to me.")]
    wait_jobs_settled(client, pid)
    run = _run_by_title("Squares")
    outs = ((run or {}).get("metadata") or {}).get("run", {}).get("outputs") or []
    dv = _durable(client, run["id"]) if run else {"files": []}
    plot_harvested = (any("squares.png" in str(o.get("label", "")) for o in outs)
                      or any(f["rel"].endswith("squares.png") for f in dv["files"]))
    caps.append(drive_turn(client, pid, tid,
        "That looks good — pin/save it as a Result titled 'Squares curve' "
        "with a one-line interpretation of the shape."))
    wait_jobs_settled(client, pid)
    results = find_entities(type="result", not_deleted=True)
    return caps, [
        ("remote step ran on hpc", "hpc" in _site_ran(caps)),
        ("the plot was harvested back into the run's outputs", plot_harvested),
        ("a Result entity now exists after pinning", bool(results)),
    ]


@scenario("mn_external_ref_inject")
def mn_external_ref_inject(client, pid, tid):
    """Data injection via EXTERNAL REFERENCE: a file already on the remote is
    registered as a dataset (reference-in-place, no copy), then compute on
    that machine reads it by its declared home path."""
    hssh(f"mkdir -p {R_DATA} && (echo k,v; seq 1 300 | "
         f"awk '{{print $1\",\"($1*4)%9}}') > {R_DATA}/ref_table.csv")
    total = sum((i * 4) % 9 for i in range(1, 301))
    caps = [drive_turn(client, pid, tid,
        f"The file {R_DATA}/ref_table.csv already lives on machine 'hpc'. "
        f"Register it as a dataset named 'Ref Table' by REFERENCE — do not "
        f"copy it here. Then, running ON hpc, report the sum of its v column.")]
    full = all_text(caps) + "\n" + thread_text(client, pid, tid)
    reg = tools_named(caps, "register_dataset")
    ds = [e for e in find_entities(type="dataset", not_deleted=True)
          if "ref table" in (e.get("title") or "").lower()]
    md = (ds[0].get("metadata") if ds else {}) or {}
    home = md.get("home") or md.get("weft_home") or {}
    return caps, [
        ("registered as a dataset", bool(ds)),
        ("by reference on hpc (home recorded, no copy)",
         home.get("site") == "hpc"),
        ("computed ON hpc", "hpc" in _site_ran(caps)),
        ("correct sum reported", str(total) in full),
    ]


@scenario("mn_background_monitor")
def mn_background_monitor(client, pid, tid):
    """Genuinely-BACKGROUND remote job (long step): the agent should submit
    and END ITS TURN (deferred contract), be resumed on completion, and
    report the result — NOT sit in a get_job_status polling loop."""
    caps = [drive_turn(client, pid, tid,
        "Run a BACKGROUND job on machine 'hpc' (it's a longer step): sleep "
        "for 25 seconds, then compute and print the sum of 1..2000. Submit "
        "it in the background — don't wait around; tell me when it's done.")]
    # deferred: the answer arrives via a continuation turn after the job lands
    full0 = all_text(caps)
    settled = wait_jobs_settled(client, pid, timeout_s=180)
    full = full0 + "\n" + thread_text(client, pid, tid)
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("background") and t["input"].get("site") == "hpc"]
    polls = len(tools_named(caps, "get_job_status"))
    return caps, [
        ("submitted a background job on hpc", bool(bg)),
        ("did NOT poll excessively (<=5 status checks)", polls <= 5),
        ("job settled", settled),
        ("final result reported (via continuation)", str(sum(range(1, 2001))) in full),
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
    # durable: True — the scenario asserts kept-IN-PLACE badges; without the
    # declaration the (correct) no-durable policy ships keepers home instead
    c.sync_call("register_site", "hpc", "slurm",
                {"root": "/home/physicist/.weft", "host": "127.0.0.1",
                 "port": conn["port"], "user": "physicist", "durable": True,
                 "ssh_opts": ["-i", f"{conn['keydir']}/id_ed25519",
                              "-o", "StrictHostKeyChecking=no",
                              "-o", "UserKnownHostsFile=/dev/null",
                              "-o", "IdentitiesOnly=yes"]})
    print("[mn] hpc (docker slurm, detached) registered")

    from fastapi.testclient import TestClient
    from main import app
    scenarios = [(fn._scenario, fn) for fn in
                 [mn_size_up, mn_hop_chain, mn_status_surfaces, mn_honesty,
                  mn_isolated_env_remote, mn_crash_fix_rerun, mn_fanout_gather,
                  mn_pin_remote_result, mn_external_ref_inject,
                  mn_background_monitor]]
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
