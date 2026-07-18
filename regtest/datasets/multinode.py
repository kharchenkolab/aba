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
M_DATA = "/home/pkharchenko/aba-mn2-data"         # ON mendel (real remote ssh)
M_ROOT = "/home/pkharchenko/aba-mn2-weft"
MENDEL_OK = False                                 # set in main() if reachable+registered


def mssh(cmd: str):
    """Run a command ON mendel (the real second remote site)."""
    import subprocess
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "mendel", cmd],
                          capture_output=True, text=True, timeout=120)


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


def _thread_raw(client, pid, tid):
    import json as _json
    r = client.get(f"/api/messages?thread_id={tid}&project_id={pid}")
    if r.status_code != 200:
        return ""
    parts = []
    for m in r.json():
        c = m.get("content") or m.get("text") or ""
        parts.append(c if isinstance(c, str) else _json.dumps(c))
    return "\n".join(parts)


def _denum(s):
    """Normalize numbers for matching: drop thousands separators so a check
    for '12070100' matches an agent's rendered '12,070,100'."""
    import re
    return re.sub(r"(?<=\d),(?=\d)", "", s)


def thread_text(client, pid, tid, settle_s=120):
    """ALL text on the thread — including deferred-continuation turns that land
    AFTER a drive_turn's stream ends (a background job's result arrives as a
    new assistant message). Waits for the project's jobs to settle first."""
    wait_jobs_settled(client, pid, timeout_s=settle_s)
    time.sleep(8)          # continuation turn writes after the job flips
    return _thread_raw(client, pid, tid)


def wait_for_text(client, pid, tid, needle, timeout_s=180):
    """Poll the thread until `needle` (number-normalized) appears — for results
    that arrive in a DEFERRED continuation turn, which starts only AFTER the
    producer jobs settle and then needs several model round-trips (jobs-settled
    + a fixed sleep is NOT enough; the gather runs later). Returns the full
    thread text once found, else after timeout."""
    wait_jobs_settled(client, pid, timeout_s=min(timeout_s, 180))
    t0 = time.time()
    target = _denum(str(needle))
    full = ""
    while time.time() - t0 < timeout_s:
        full = _denum(_thread_raw(client, pid, tid))
        if target in full:
            return full
        time.sleep(6)
    return full


def _site_ran(caps):
    """site= values of run_python/run_r calls that did NOT error — a mere
    invocation must not count as 'ran on the site' (a failed call would
    otherwise pass site-routing checks vacuously). A call whose result the
    stream didn't carry (deferred envelope) counts — the scenario's own
    output checks cover its success."""
    sites = []
    for t in tools_named(caps, "run_python") + tools_named(caps, "run_r"):
        s = t["input"].get("site")
        res = t.get("result")
        ok = (not isinstance(res, dict)
              or res.get("status") not in ("error", "cancelled"))
        if s and ok:
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
    txt = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
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
    """(iii) After a remote run produces a LARGE output that STAYS on the node
    (small outputs auto-come-home by design), the SURFACES the cards render
    from must show it as SAFE ON HPC. Mechanism-agnostic: the agent may keep
    the run output in place OR register it as a durable-home dataset — both
    are valid 'safe on hpc' outcomes; we assert whichever surface reflects it,
    plus the project ledger, on the exact JSON the cards read.
    Scoped to entities THIS scenario creates — the shared single-DB study
    accumulates entities across scenarios, and a stale hpc-homed entity from
    an earlier one must not satisfy these checks."""
    pre = ({e["id"] for e in find_entities(type="analysis", not_deleted=True)}
           | {e["id"] for e in find_entities(type="dataset", not_deleted=True)})
    caps = [drive_turn(client, pid, tid,
        "Open an analysis run titled 'Remote production'. Then run a BACKGROUND "
        "job on machine 'hpc' that writes a LARGE ~60 MB file called big.bin "
        "in the run's working directory (e.g. 60*1024*1024 bytes). It's big — "
        "make sure it's kept SAFE on hpc without copying it here.")]
    wait_jobs_settled(client, pid)

    def _on_hpc_somewhere():
        # (a) a NEW run whose durable view marks big.bin kept on hpc
        for r in find_entities(type="analysis", not_deleted=True):
            if r["id"] in pre:
                continue
            dv = _durable(client, r["id"])
            for f in dv["files"]:
                if f["rel"].endswith("big.bin") and f.get("site") == "hpc":
                    return ("run", r["id"], f)
        # (b) a NEW dataset whose durable home is on hpc
        for e in find_entities(type="dataset", not_deleted=True):
            if e["id"] in pre:
                continue
            home = ((e.get("metadata") or {}).get("home")
                    or (e.get("metadata") or {}).get("weft_home") or {})
            if home.get("site") == "hpc":
                return ("dataset", e["id"], None)
        return (None, None, None)

    kind, eid, frow = None, None, None
    t0 = time.time()
    while time.time() - t0 < 180:
        kind, eid, frow = _on_hpc_somewhere()
        if kind:
            break
        time.sleep(6)
    led = client.get(f"/api/projects/{pid}/data-ledger").json()
    checks = [
        ("big output shown SAFE ON HPC (run keep or dataset home)", bool(kind)),
        ("ledger sees hpc as a remote site", "hpc" in (led.get("remote_sites") or [])),
    ]
    if kind == "run":
        checks.append(("run durable badge reads 'kept ✓ · on hpc'",
                       str((frow or {}).get("badge", "")).startswith("kept ✓")
                       and "hpc" in (frow or {}).get("badge", "")))
        bb = client.post(f"/api/runs/{eid}/bring-back")
        time.sleep(8)
        dv2 = _durable(client, eid)
        checks.append(("big file servable locally after bring-back", any(
            f.get("url") for f in dv2["files"] if f["rel"].endswith("big.bin"))))
    caps.append(drive_turn(client, pid, tid,
        "Where does the big file live now, and is it safe? One line."))
    atxt = caps[-1]["text"].lower()
    checks.append(("agent names hpc + safety", "hpc" in atxt
                   and any(w in atxt for w in ("safe", "kept", "retained", "durable"))))
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
    # dedicated dir with EXACTLY ONE csv, so the debug is unambiguous — a
    # shared dir accumulates other scenarios' files and the agent (correctly)
    # refuses to guess among multiple val-column candidates
    cdir = "/home/physicist/aba-mn-crash"
    hssh(f"rm -rf {cdir} && mkdir -p {cdir} && (echo idx,val; seq 1 60 | "
         f"awk '{{print $1\",\"($1*5)%11}}') > {cdir}/vals_v2.csv")
    total = sum((i * 5) % 11 for i in range(1, 61))
    caps = [drive_turn(client, pid, tid,
        f"On machine 'hpc', read the csv in {cdir} — I think it's called "
        f"vals.csv — and report the sum of its val column. There is exactly "
        f"one csv in that folder; if the name is wrong, use the one that's "
        f"there. Run it directly (not as a background job).")]
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
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
    variants in parallel (node + local mix), then a gather step. The gather
    runs in a DEFERRED continuation turn after all producers finish — so we
    poll for the total (number-normalized) rather than a fixed settle.

    NOTE (recorded, not a product bug): there is no sibling-join barrier —
    each job's continuation fires independently; the first-completing one
    wakes the agent while siblings may still run. It works because the agent
    defensively re-checks the others; a true barrier would need a turn-group
    id to fire a single gather on the LAST sibling."""
    total = 338350 + 2686700 + 9045050
    caps = [drive_turn(client, pid, tid,
        "Run three INDEPENDENT background jobs, in parallel (submit all "
        "before waiting): each computes the sum of i*i for i from 1 to N, "
        "for N = 100, 200 and 300. Run at least one of them on machine "
        "'hpc' and at least one locally. When all three are done, compute "
        "the TOTAL of the three results and report it.")]
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("background")]
    sites = [t["input"].get("site") for t in bg]
    full = _denum(all_text(caps)) + "\n" + wait_for_text(client, pid, tid, total)
    return caps, [
        ("three background jobs submitted", len(bg) >= 3),
        ("at least one on hpc", "hpc" in sites),
        ("at least one local", any(not s for s in sites)),
        ("correct total reported (via gather continuation)", str(total) in full),
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
    # snapshot BEFORE the pin turn — a Result left by an earlier scenario in
    # the shared single-DB study must not satisfy "pinning worked"
    pre_results = {e["id"] for e in find_entities(type="result", not_deleted=True)}
    caps.append(drive_turn(client, pid, tid,
        "That looks good — pin/save it as a Result titled 'Squares curve' "
        "with a one-line interpretation of the shape."))
    wait_jobs_settled(client, pid)
    new_results = [e for e in find_entities(type="result", not_deleted=True)
                   if e["id"] not in pre_results]
    return caps, [
        ("remote step ran on hpc", "hpc" in _site_ran(caps)),
        ("the plot was harvested back into the run's outputs", plot_harvested),
        ("THIS pin created a Result entity", bool(new_results)),
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
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
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
    answer = sum(range(1, 2001))
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("background") and t["input"].get("site") == "hpc"]
    polls = len(tools_named(caps, "get_job_status"))
    # the answer arrives in a deferred continuation — poll for it (normalized)
    full = _denum(all_text(caps)) + "\n" + wait_for_text(client, pid, tid, answer)
    return caps, [
        ("submitted a background job on hpc", bool(bg)),
        ("did NOT poll excessively (<=5 status checks)", polls <= 5),
        ("final result reported (via continuation)", str(answer) in full),
    ]


@scenario("mn_provenance_after_chain")
def mn_provenance_after_chain(client, pid, tid):
    """Provenance across machines: a figure produced on hpc, pinned to a
    Result, must trace back to WHERE it ran. Validates the remote-sync exec
    record carries the placement block (ran on hpc) into the entity graph."""
    caps = [drive_turn(client, pid, tid,
        "Open a run 'Prov check'. On machine 'hpc', compute z = i%7 for "
        "i in 1..120 and make a bar plot saved as bars.png. Bring it back.")]
    wait_jobs_settled(client, pid)
    # scenario-scoped: only a Result created by THIS pin turn counts
    pre_results = {e["id"] for e in find_entities(type="result", not_deleted=True)}
    caps.append(drive_turn(client, pid, tid,
        "Pin that as a Result titled 'Bars' with a short interpretation."))
    wait_jobs_settled(client, pid)
    results = [e for e in find_entities(type="result", not_deleted=True)
               if e["id"] not in pre_results]
    # provenance question — the placement block should let the agent name hpc
    caps.append(drive_turn(client, pid, tid,
        "For the 'Bars' result: which machine actually produced it, and how "
        "was it made? Be specific about where it ran."))
    ptext = caps[-1]["text"].lower()
    return caps, [
        ("remote step ran on hpc", "hpc" in _site_ran(caps)),
        ("a Result exists", bool(results)),
        ("agent names hpc as where it ran (placement provenance)",
         "hpc" in ptext),
    ]


@scenario("mn_preflight_disconnect")
def mn_preflight_disconnect(client, pid, tid):
    """The safety-ledger story as a conversation: a dataset lives with its
    durable home ON hpc; the user asks what's at risk before disconnecting.
    The agent must consult the ledger (data_safety_summary) and name the
    hpc-resident item — grounded, not guessed."""
    hssh(f"mkdir -p {R_DATA} && (echo a,b; seq 1 200 | "
         f"awk '{{print $1\",\"($1%5)}}') > {R_DATA}/homed.csv")
    caps = [drive_turn(client, pid, tid,
        f"Register {R_DATA}/homed.csv on machine 'hpc' as a dataset "
        f"'Homed Table' by reference (leave it there).")]
    ds = [e for e in find_entities(type="dataset", not_deleted=True)
          if "homed table" in (e.get("title") or "").lower()]
    caps.append(drive_turn(client, pid, tid,
        "I'm thinking of disconnecting the machine 'hpc'. Before I do — is "
        "anything in this project at risk if I disconnect it? Check properly."))
    used = tools_named(caps, "data_safety_summary") + tools_named(caps, "list_compute_sites")
    txt = caps[-1]["text"].lower()
    return caps, [
        ("dataset home is on hpc", bool(ds) and (((ds[0].get("metadata") or {})
            .get("home") or {}).get("site") == "hpc")),
        ("agent consulted the safety ledger / sites", bool(used)),
        ("agent flags the hpc-resident data", "hpc" in txt
         and any(w in txt for w in ("risk", "lose", "access", "homed", "disconnect"))),
    ]


@scenario("mn_reference_drift")
def mn_reference_drift(client, pid, tid):
    """External reference on a remote machine drifts: register a file on hpc
    by reference, then the file CHANGES on hpc; the agent re-checks and
    reports the drift instead of trusting stale content."""
    hssh(f"mkdir -p {R_DATA} && (echo x; seq 1 100) > {R_DATA}/drift.csv")
    caps = [drive_turn(client, pid, tid,
        f"Register {R_DATA}/drift.csv on machine 'hpc' as dataset 'Drift Set' "
        f"by reference.")]
    ds = [e for e in find_entities(type="dataset", not_deleted=True)
          if "drift set" in (e.get("title") or "").lower()]
    # the source changes underneath us on the remote
    hssh(f"(echo x; seq 1 500) > {R_DATA}/drift.csv")
    caps.append(drive_turn(client, pid, tid,
        "Has the source of the 'Drift Set' dataset changed since we "
        "registered it? Check the actual file on hpc and tell me."))
    checked = tools_named(caps, "check_import")
    txt = caps[-1]["text"].lower()
    return caps, [
        ("dataset registered by reference on hpc", bool(ds)),
        ("agent re-checked the remote source", bool(checked)),
        ("agent reports it CHANGED", any(w in txt for w in
            ("changed", "drift", "differ", "modified", "no longer match",
             "grown", "larger", "updated"))),
    ]


@scenario("mn_gpu_routing")
def mn_gpu_routing(client, pid, tid):
    """Placement plumbing: a GPU-flagged step on hpc must carry the GPU
    resource so weft lands it on a GPU partition. (The fixture's GPU is a
    stub, so we test ROUTING — est_gpu → gpus resource → gpu partition —
    not GPU math.)"""
    caps = [drive_turn(client, pid, tid,
        "Run a short step ON machine 'hpc' that needs a GPU (flag it as a "
        "GPU workload). It only has to print its hostname — I'm confirming "
        "that GPU-flagged work routes to a GPU node. Report where it ran.")]
    wait_jobs_settled(client, pid)
    gpu_runs = [t for t in tools_named(caps, "run_python")
                if t["input"].get("site") == "hpc" and t["input"].get("est_gpu")]
    txt = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid)).lower()
    # confirm the task actually requested a GPU (job row estimate)
    from core.graph.jobs import _row_to_job
    import sqlite3
    from core.graph._schema import active_db_path
    requested_gpu = False
    try:
        c = sqlite3.connect(active_db_path()); c.row_factory = sqlite3.Row
        for r in c.execute("SELECT * FROM jobs").fetchall():
            j = _row_to_job(r); p = j.get("params") or {}
            if p.get("site") == "hpc" and (p.get("estimate") or {}).get("gpu"):
                requested_gpu = True
        c.close()
    except Exception:  # noqa: BLE001
        pass
    return caps, [
        ("agent flagged the step as GPU (est_gpu) on hpc", bool(gpu_runs)),
        ("the job requested a GPU resource", requested_gpu),
        ("agent reports it ran on hpc", "hpc" in txt),
    ]


@scenario("mn_rerun_asis_recomputes")
def mn_rerun_asis_recomputes(client, pid, tid):
    """The memo-nonce design: re-running IDENTICAL remote code must actually
    RECOMPUTE (a fresh weft task), not silently return a memo-cached result.
    Two runs of the same code → two DISTINCT weft task ids. Snapshot-diffed:
    the shared single-DB study accumulates hpc jobs across scenarios, and a
    global count could NEVER fail — the whole point is that THESE two turns
    each minted a fresh task."""
    def _hpc_wids():
        from core.graph.jobs import _row_to_job
        import sqlite3
        from core.graph._schema import active_db_path
        wids = set()
        try:
            c = sqlite3.connect(active_db_path()); c.row_factory = sqlite3.Row
            for r in c.execute("SELECT * FROM jobs").fetchall():
                p = (_row_to_job(r).get("params") or {})
                if p.get("site") == "hpc" and p.get("weft_id"):
                    wids.add(p["weft_id"])
            c.close()
        except Exception:  # noqa: BLE001
            pass
        return wids

    pre_wids = _hpc_wids()
    caps = [drive_turn(client, pid, tid,
        "On machine 'hpc', compute and print the sum of 1..1234. Run it "
        "directly.")]
    caps.append(drive_turn(client, pid, tid,
        "Run that exact same computation on 'hpc' again — I want it actually "
        "re-run, not a cached answer."))
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    new_wids = _hpc_wids() - pre_wids
    hpc_runs = [t for t in tools_named(caps, "run_python")
                if t["input"].get("site") == "hpc"]
    return caps, [
        ("both runs targeted hpc", len(hpc_runs) >= 2),
        ("correct sum reported", str(sum(range(1, 1235))) in full),
        ("THESE re-runs minted DISTINCT weft tasks (no memo collision)",
         len(new_wids) >= 2),
    ]


@scenario("mn_data_gravity_recall")
def mn_data_gravity_recall(client, pid, tid):
    """Multi-turn context durability: a dataset is registered on hpc; then
    several UNRELATED small local turns pass; then a heavy step over that
    dataset must route to hpc WITHOUT being re-told the machine (data gravity
    + the ambient remote-site context line), and the agent recalls where it
    lives. Tests whether provisioning survives a long gap without confusing
    the agent."""
    hssh(f"mkdir -p {R_DATA} && (echo p,q; seq 1 400 | "
         f"awk '{{print $1\",\"($1*6)%13}}') > {R_DATA}/gravity.csv")
    total = sum((i * 6) % 13 for i in range(1, 401))
    caps = [drive_turn(client, pid, tid,
        f"Register {R_DATA}/gravity.csv on machine 'hpc' as dataset "
        f"'Gravity Set' by reference.")]
    # unrelated small local work — the 'long gap'
    caps.append(drive_turn(client, pid, tid,
        "Quick unrelated thing: what's 17 * 23? Just tell me."))
    caps.append(drive_turn(client, pid, tid,
        "And make a tiny local list of the first 5 even numbers."))
    caps.append(drive_turn(client, pid, tid,
        "One more: reverse the string 'analytics' for me."))
    # now the heavy step — WITHOUT naming the machine
    caps.append(drive_turn(client, pid, tid,
        "OK — now do a heavy summation over the 'Gravity Set' dataset: sum "
        "its q column. It's a big/heavy step, so run it in the most sensible "
        "place given where the data lives. Tell me the sum and where it ran."))
    heavy = caps[-1]
    ran_hpc = "hpc" in _site_ran([heavy])
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    ftext = _denum(heavy["text"]).lower()
    return caps, [
        ("dataset registered on hpc", any(
            ((e.get("metadata") or {}).get("home") or {}).get("site") == "hpc"
            for e in find_entities(type="dataset", not_deleted=True)
            if "gravity set" in (e.get("title") or "").lower())),
        ("routed the heavy step to hpc WITHOUT being re-told", ran_hpc),
        ("correct sum reported", str(total) in full),
        ("agent recalls it ran on hpc", "hpc" in ftext),
    ]


@scenario("mn_conflicting_gravity")
def mn_conflicting_gravity(client, pid, tid):
    """Two real sites, conflicting data gravity: a LARGE dataset lives on
    mendel; the user asks to compute 'on hpc'. The agent must NOT silently
    haul big data across machines — it should compute where the data is
    (mendel) or explicitly surface the transfer cost. (Skips if mendel isn't
    available.)"""
    if not MENDEL_OK:
        return [], [("mendel available (scenario skipped otherwise)", False)]
    # a ~70 MB file of newline-terminated integers 1..N: the COMPUTE needs the
    # whole big file (so data gravity is real), and its sum is deterministic
    n = 3_000_000
    mssh(f"mkdir -p {M_DATA} && seq 1 {n} > {M_DATA}/big_nums.txt")
    total = n * (n + 1) // 2
    caps = [drive_turn(client, pid, tid,
        f"A large (~{'{:.0f}'.format(20)} MB+) file big_nums.txt lives on machine "
        f"'mendel' at {M_DATA} — it's one integer per line, 1 up to {n}. I "
        f"want the SUM of every number in it. I was going to say run it on "
        f"'hpc', but the file is big — do whatever avoids hauling it across "
        f"machines. Give me the sum and say where you ran it, and why.")]
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    txt = full.lower()
    ran = _site_ran(caps)
    pulled_big = any(int(t["input"].get("max_bytes") or 0) > 40_000_000
                     for t in tools_named(caps, "fetch_dataset"))
    return caps, [
        ("computed at the data on mendel (not hpc)",
         "mendel" in ran and "hpc" not in ran),
        ("reasoned about data gravity / avoiding the transfer",
         any(w in txt for w in ("where the data", "avoid moving", "avoid hauling",
             "transfer", "data lives", "on mendel", "gravity"))),
        ("correct sum reported", str(total) in full),
        ("did not haul the big file across", not pulled_big),
    ]


@scenario("mn_cross_thread_separation")
def mn_cross_thread_separation(client, pid, tid):
    """Two threads, two sites: thread A works on hpc, thread B on mendel. The
    agent must not cross-wire where each thread's work ran. (Skips if mendel
    isn't available.)"""
    if not MENDEL_OK:
        return [], [("mendel available (scenario skipped otherwise)", False)]
    tidB = client.post("/api/threads",
                       json={"project_id": pid, "title": "thread B"}).json()["id"]
    capsA = drive_turn(client, pid, tid,
        "On machine 'hpc', compute and print the sum of 1..111. Run it there.")
    capsB = drive_turn(client, pid, tidB,
        "On machine 'mendel', compute and print the sum of 1..222. Run it there.")
    qA = drive_turn(client, pid, tid,
        "Which machine did the work in THIS conversation run on? One word.")
    qB = drive_turn(client, pid, tidB,
        "Which machine did the work in THIS conversation run on? One word.")
    caps = [capsA, capsB, qA, qB]
    aran = _site_ran([capsA]); bran = _site_ran([capsB])
    aTxt = qA["text"].lower(); bTxt = qB["text"].lower()
    return caps, [
        ("thread A ran on hpc", "hpc" in aran),
        ("thread B ran on mendel", "mendel" in bran),
        ("thread A recalls hpc (not mendel)", "hpc" in aTxt and "mendel" not in aTxt),
        ("thread B recalls mendel (not hpc)", "mendel" in bTxt and "hpc" not in bTxt),
    ]


@scenario("mn_mid_chain_steering")
def mn_mid_chain_steering(client, pid, tid):
    """Multi-turn steering: a chain starts on hpc, then the user RETARGETS the
    next step to mendel mid-flight. The agent must retarget cleanly, carry the
    prior stage's value across (not lose it), and land the right final number.
    (Skips if mendel isn't available.)"""
    if not MENDEL_OK:
        return [], [("mendel available (scenario skipped otherwise)", False)]
    caps = [drive_turn(client, pid, tid,
        "STEP 1: on machine 'hpc', compute the sum of 1..300 and tell me the "
        "number. Keep it handy for a follow-up.")]
    s1 = sum(range(1, 301))          # 45150
    caps.append(drive_turn(client, pid, tid,
        "STEP 2: actually, run the NEXT step on 'mendel' instead of hpc — "
        "take that step-1 number and multiply it by 3. Report the result and "
        "confirm which machine each step ran on."))
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    s1_ran = _site_ran([caps[0]]); s2_ran = _site_ran([caps[1]])
    txt = full.lower()
    return caps, [
        ("step 1 ran on hpc", "hpc" in s1_ran),
        ("step 2 retargeted to mendel", "mendel" in s2_ran),
        ("step-1 value carried across (not lost)", str(s1) in full),
        ("correct final (x3) reported", str(s1 * 3) in full),
        ("agent confirms both machines", "hpc" in txt and "mendel" in txt),
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

    # optional SECOND real site (mendel over ssh) for the cross-site scenarios;
    # gracefully skipped when unreachable
    global MENDEL_OK
    if mssh("echo ok").stdout.strip() == "ok":
        try:
            r = c.sync_call("register_site", "mendel", "ssh",
                            {"root": M_ROOT, "host": "mendel", "durable": True})
            MENDEL_OK = r.get("site") == "mendel"
            print("[mn] mendel (real ssh, detached) registered" if MENDEL_OK
                  else f"[mn] mendel registration returned {r}")
        except Exception as e:  # noqa: BLE001
            print("[mn] mendel registration failed — cross-site scenarios skip:", e)
    else:
        print("[mn] mendel unreachable — cross-site scenarios skip")

    from fastapi.testclient import TestClient
    from main import app
    scenarios = [(fn._scenario, fn) for fn in
                 [mn_size_up, mn_hop_chain, mn_status_surfaces, mn_honesty,
                  mn_isolated_env_remote, mn_crash_fix_rerun, mn_fanout_gather,
                  mn_pin_remote_result, mn_external_ref_inject,
                  mn_background_monitor, mn_provenance_after_chain,
                  mn_preflight_disconnect, mn_reference_drift,
                  mn_gpu_routing, mn_rerun_asis_recomputes,
                  mn_data_gravity_recall, mn_conflicting_gravity,
                  mn_cross_thread_separation, mn_mid_chain_steering]]
    try:
        with TestClient(app) as client:
            try:
                for name, fn in scenarios:
                    if only and name not in only:
                        continue
                    run_scenario(client, name, fn)
            finally:
                for site in ("hpc", "mendel"):
                    try:
                        ad.get_compute().sync_call("site_unregister", site)
                    except Exception:  # noqa: BLE001
                        pass
                print("[cleanup] sites unregistered")
    finally:
        out = hssh("rm -rf /home/physicist/aba-mn-data "
                   "/home/physicist/aba-mn-crash && echo cleaned")
        print("[cleanup] hpc data dirs:", out.stdout.strip() or out.stderr[-120:])
        if MENDEL_OK:
            mout = mssh(f"rm -rf {M_ROOT} {M_DATA} && echo cleaned")
            print("[cleanup] mendel dirs:", mout.stdout.strip() or mout.stderr[-120:])
    print("\nMULTINODE:", "ALL PASS" if all(ok for _, ok in RESULTS)
          else "FAILURES: " + ", ".join(n for n, ok in RESULTS if not ok))
    sys.exit(0 if all(ok for _, ok in RESULTS) else 1)


if __name__ == "__main__":
    main()
