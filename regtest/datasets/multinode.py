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
    drive_turn, resume_turn, tools_named, all_text, run_scenario, scenario,
    RESULTS,
)
from core.graph.entities import find_entities, get_entity  # noqa: E402

R_DATA = "/home/physicist/aba-mn-data/readings"   # ON the hpc fixture
M_DATA = "/home/pkharchenko/aba-mn2-data"         # ON mendel (real remote ssh)
M_ROOT = "/home/pkharchenko/aba-mn2-weft"
MENDEL_OK = False                                 # set in main() if reachable+registered
C_ROOT = "/users/peter.kharchenko/aba-mn-cbe-weft"  # ON cbe.next (real slurm)
CBE_OK = False                                    # set in main() if reachable+registered


def _ssh_run(argv: list, timeout: int):
    """subprocess.run that DEGRADES on timeout instead of raising — a dark
    machine must read as unreachable (rc 124, empty stdout), never crash the
    whole suite at bootstrap (long_arc died to a transient 2-min mendel
    sshd throttle, 2026-07-19)."""
    import subprocess
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, stdout="",
                                           stderr="ssh timeout")


def mssh(cmd: str):
    """Run a command ON mendel (the real second remote site)."""
    return _ssh_run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                     "mendel", cmd], 120)


def cssh(cmd: str):
    """Run a command ON cbe.next (the real slurm cluster; ProxyJump via
    ~/.ssh/config, so latency is jump-host-shaped — keep calls few)."""
    return _ssh_run(["ssh", "-o", "BatchMode=yes",
                     "-o", "ConnectTimeout=20", "cbe.next", cmd], 180)


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
    conn = _cluster_conn()
    return _ssh_run(
        ["ssh", "-i", f"{conn['keydir']}/id_ed25519",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
         "-p", str(conn["port"]), "physicist@127.0.0.1", cmd], 120)


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


def _thread_raw(client, pid, tid, role=None):
    import json as _json
    r = client.get(f"/api/messages?thread_id={tid}&project_id={pid}")
    if r.status_code != 200:
        return ""
    parts = []
    for m in r.json():
        # role scoping matters for honesty needles: the harness's OWN prompt
        # contains the very words being asserted ("timeout", the site name…),
        # so an all-roles search can never fail (found by the recheck review)
        if role and (m.get("role") or "") != role:
            continue
        c = m.get("content") or m.get("text") or ""
        parts.append(c if isinstance(c, str) else _json.dumps(c))
    return "\n".join(parts)


def _denum(s):
    """Normalize numbers for matching: drop thousands separators so a check
    for '12070100' matches an agent's rendered '12,070,100'."""
    import re
    return re.sub(r"(?<=\d),(?=\d)", "", s)


def thread_text(client, pid, tid, settle_s=120, role=None):
    """ALL text on the thread — including deferred-continuation turns that land
    AFTER a drive_turn's stream ends (a background job's result arrives as a
    new assistant message). Waits for the project's jobs to settle first.
    role='assistant' scopes to the AGENT's words — required whenever the
    asserted needle also appears in the harness's own prompt."""
    wait_jobs_settled(client, pid, timeout_s=settle_s)
    time.sleep(8)          # continuation turn writes after the job flips
    return _thread_raw(client, pid, tid, role=role)


def agent_text(client, pid, tid, settle_s=120):
    """Only the agent's messages — the falsifiable surface for honesty checks."""
    return thread_text(client, pid, tid, settle_s=settle_s, role="assistant")


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


def _site_ran_strict(caps, site):
    """STRICT companion to _site_ran (cross-cutting rec #2): the site actually
    ran a step to a clean finish — at least ONE run_python/run_r call on `site`
    whose RESULT envelope reports returncode == 0. Unlike _site_ran, a call
    carrying NO result envelope (a deferred/background continuation) does NOT
    count. Use for core placement claims where a silently-skipped or
    never-observed step must fail rather than pass vacuously. NOTE: local +
    remote-SESSION (env='system') results carry `returncode`; a remote fresh-
    process sync result exposes `status=='ok'` instead — so this helper is
    exact for the session/local lanes."""
    for t in tools_named(caps, "run_python") + tools_named(caps, "run_r"):
        if t["input"].get("site") != site:
            continue
        res = t.get("result")
        if isinstance(res, dict) and res.get("returncode") == 0:
            return True
    return False


def _jobs_snapshot():
    """Every job row in the active project DB (id, status, params, log_tail) —
    the substrate truth a background job's OWN captured output lands in
    (runner writes stdout[-1500:] to log_tail). Same sqlite path the timeout /
    gpu-routing / cancel scenarios read."""
    import sqlite3
    from core.graph.jobs import _row_to_job
    from core.graph._schema import active_db_path
    try:
        c = sqlite3.connect(active_db_path()); c.row_factory = sqlite3.Row
        rows = [_row_to_job(r) for r in c.execute("SELECT * FROM jobs").fetchall()]
        c.close()
        return rows
    except Exception:  # noqa: BLE001
        return []


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
    # data-dependent oracle: the column sum of (i*7)%13 over 1..800 can't be
    # derived from the prompt (which never states it) — the promised compute
    # actually happened only if the true number reaches the transcript
    # (sibling mn_hop_chain asserts `str(total) in txt` the same way). The
    # prior checks never verified ANY number the heavy step was asked to
    # produce, so a no-op step passed. TODO(strengthen): also assert the sum
    # in the hpc step's own result["stdout"] + hssh test -s the summary file —
    # needs the run's working-dir path (this prompt opens no named run).
    total = sum((i * 7) % 13 for i in range(1, 801))
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    local_touches_big = any(
        "block.bin" in (t["input"].get("code") or "")
        for t in tools_named(caps, "run_python") if not t["input"].get("site"))
    return caps, [
        ("describe_compute consulted", bool(tools_named(caps, "describe_compute"))),
        ("ran the step ON hpc (site=)", "hpc" in _site_ran(caps)),
        ("no LOCAL code touched the big file", not local_touches_big),
        ("reports where it ran", "hpc" in txt),
        ("correct column-sum reported (data-dependent oracle)",
         str(total) in full),
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
    # TODO(strengthen): also assert each stage total in that step's own
    # result["stdout"] + hssh "test -s <run dir>/stage1.txt". SKIPPED: the
    # remote steps write stage files into the run working dir whose path this
    # prompt never exposes (no named run), and the steps aren't asked to PRINT
    # the number — so a stdout/test-s oracle isn't reliably groundable here.
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
        # 60 MB over ssh takes longer than one breath — poll (the fixed 8s
        # sleep failed the check while the pull was still in flight)
        served = False
        bb_deadline = time.time() + 180
        while time.time() < bb_deadline and not served:
            time.sleep(8)
            dv2 = _durable(client, eid)
            served = any(f.get("url") for f in dv2["files"]
                         if f["rel"].endswith("big.bin"))
        checks.append(("big file servable locally after bring-back", served))
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
    # AGENT-scoped: the prompt itself names the bogus machine, so an all-roles
    # search could satisfy the honesty needle without the agent saying a word.
    # _denum: the answer renders as 25,502,500 as often as 25502500.
    full = _denum(all_text(caps) + "\n" + agent_text(client, pid, tid))
    txt = full.lower()
    answer = str(sum(i ** 3 for i in range(1, 101)))
    # TODO(strengthen): also require `answer` in a non-error step's own
    # result["stdout"] (proves it ran SOMEWHERE for real). SKIPPED: the ask is
    # background AND "run it wherever you can" — the step may be a deferred bg
    # job (no result envelope in caps) or a local fallback, so neither the
    # step result nor a single job-row query reliably scopes it.
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
    # SUBSTRATE (sibling mn_background_monitor job-row read; runner writes the
    # step's stdout[-1500:] to log_tail): the background hpc job that imported
    # click in env 'numtools' really ran — its OWN captured output carries a
    # real dotted version. Narration can echo "click 8.x" with the env never
    # realized and the import never executed; the DB row cannot. Env name
    # 'numtools' scopes the row to THIS scenario in the shared DB.
    import re as _re
    env_jobs = [j for j in _jobs_snapshot()
                if (j.get("params") or {}).get("site") == "hpc"
                and (j.get("params") or {}).get("env") == "numtools"]
    env_job_done = any(j.get("status") == "done" for j in env_jobs)
    version_in_job_log = any(_re.search(r"\d+\.\d+", j.get("log_tail") or "")
                             for j in env_jobs)
    # pair the env-creation invocation with its result envelope (rec #1)
    mk_ok = any(not isinstance(t.get("result"), dict)
                or (t.get("result") or {}).get("status") not in ("error", "cancelled")
                for t in mk)
    # TODO(strengthen): also hssh "cat <run dir>/ok.txt" ~= r"\d+\.\d+" — needs
    # the remote run working-dir path (this prompt opens no named run).
    return caps, [
        ("isolated env created", bool(mk)),
        ("env creation did not error (result envelope)", bool(mk) and mk_ok),
        ("remote job ran IN that env", any(
            (t["input"].get("env") or "") == "numtools" for t in runs)),
        ("remote job in env 'numtools' settled done (substrate)", env_job_done),
        # tightened: a bare digit anywhere was vacuous — require a real dotted
        # version AND ground it in the job's own log, not only narration
        ("a real version was reported (dotted, in the job's own log)",
         version_in_job_log and _re.search(r"\d+\.\d+", full) is not None
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
    partials = [338350, 2686700, 9045050]     # sum of squares 1..100/200/300
    total = sum(partials)
    caps = [drive_turn(client, pid, tid,
        "Run three INDEPENDENT background jobs, in parallel (submit all "
        "before waiting): each computes AND PRINTS the sum of i*i for i from "
        "1 to N, for N = 100, 200 and 300. Run at least one of them on "
        "machine 'hpc' and at least one locally. When all three are done, "
        "compute the TOTAL of the three results and report it.")]
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("background")]
    sites = [t["input"].get("site") for t in bg]
    full = _denum(all_text(caps)) + "\n" + wait_for_text(client, pid, tid, total)
    # SUBSTRATE (sibling mn_net_drop_midjob job-row read + runner log_tail):
    # each parallel job REALLY ran — its row settled done AND its own captured
    # output carries its partial. A closed-form total in narration can't prove
    # three jobs ran; a silent no-op (bug5) leaves the agent free to derive the
    # missing partial. Partials are unique to this scenario, so no pre-snapshot
    # is needed (prompt now asks each job to PRINT its partial).
    rows = _jobs_snapshot()
    each_partial_from_a_done_job = all(
        any(_denum(str(p)) in _denum(j.get("log_tail") or "")
            and j.get("status") == "done" for j in rows)
        for p in partials)
    return caps, [
        ("three background jobs submitted", len(bg) >= 3),
        ("at least one on hpc", "hpc" in sites),
        ("at least one local", any(not s for s in sites)),
        ("correct total reported (via gather continuation)", str(total) in full),
        ("each partial came from its OWN done job's output (substrate)",
         each_partial_from_a_done_job),
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
    # SUBSTRATE (sibling mn_background_monitor's own class; runner log_tail):
    # the job the agent submitted must have actually produced the answer — its
    # row settled done AND its own captured output carries 2001000. A dead job
    # + the agent "helpfully" restating the closed-form sum would pass on
    # narration alone. Prompt already asks the job to print the sum.
    job_carried_answer = any(
        _denum(str(answer)) in _denum(j.get("log_tail") or "")
        and j.get("status") == "done"
        and (j.get("params") or {}).get("site") == "hpc"
        for j in _jobs_snapshot())
    return caps, [
        ("submitted a background job on hpc", bool(bg)),
        ("did NOT poll excessively (<=5 status checks)", polls <= 5),
        ("final result reported (via continuation)", str(answer) in full),
        ("the hpc job itself produced the answer (done + in its log)",
         job_carried_answer),
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
    # SUBSTRATE (sibling ui_failed_run_card reads exec_records.list_by_run;
    # weft_submitter._compute_block writes the placement/provenance block): the
    # run's exec record must actually CARRY where it ran into the graph — the
    # agent can name hpc from conversational memory with the stored record
    # empty. A remote step's compute block is weft-sourced (substrate + a real
    # node); assert it landed, not just that the agent said "hpc".
    from core.graph import exec_records as _er
    prov_run = _run_by_title("Prov check")
    placement_recorded = False
    if prov_run:
        for r in _er.list_by_run(prov_run["id"]):
            comp = (_er.get(r["exec_id"]) or {}).get("compute") or {}
            # a weft-sourced compute block is written only for a step that went
            # through the detached substrate — a local step has none. Its
            # presence proves the placement/provenance block landed in the graph
            # (not that the agent recalled 'hpc' from conversation).
            if comp.get("substrate") == "weft":
                placement_recorded = True
                break
    return caps, [
        ("remote step ran on hpc", "hpc" in _site_ran(caps)),
        ("a Result exists", bool(results)),
        ("weft placement/provenance block recorded on the run's exec record "
         "(substrate, not conversational memory)", placement_recorded),
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
    # RESULT (check_import_tool returns {stale, reason}): assert the freshness
    # tool's OWN verdict, not the agent's paraphrase — an errored/wrong "still
    # current" tool result with a hedging reply must fail. The source grew
    # (100→500 lines) so the site-side revalidate reports stale/changed.
    changed_verdict = any(
        (t.get("result") or {}).get("stale") is True
        and (t.get("result") or {}).get("reason") in ("changed", "missing")
        for t in checked)
    return caps, [
        ("dataset registered by reference on hpc", bool(ds)),
        ("agent re-checked the remote source", bool(checked)),
        ("check_import's OWN verdict is stale/changed (tool result)",
         changed_verdict),
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
    # TODO(strengthen): assert SCHEDULER-side truth — capture the new job id via
    # hssh "scontrol show jobs -o" (the mn_slurm_sized_walltime technique) and
    # check it carries the gres/accelerator request and lands on a GPU
    # partition. SKIPPED here: the fixture's GPU is a stub, so it is not
    # confirmed that scontrol on it exposes a gpu partition/gres — a wrong
    # scheduler-side assertion would false-fail. (mn_cbe_gpu does this against
    # the REAL cluster, where the partition exists.)
    return caps, [
        ("agent flagged the step as GPU (est_gpu) on hpc", bool(gpu_runs)),
        ("the job requested a GPU resource", requested_gpu),
        ("agent reports it ran on hpc", "hpc" in txt),
    ]


@scenario("mn_slurm_sized_walltime")
def mn_slurm_sized_walltime(client, pid, tid):
    """SLURM specifics against the REAL scheduler (sacct ground truth): the
    sized-only walltime doctrine live — a SIZED background job carries an
    explicit TimeLimit derived from its estimate; an UNSIZED direct step
    rides the partition default (an inflated default ask pends forever on
    capped partitions — the PartitionTimeLimit trap, verified live)."""
    def _limit_min(s: str):
        """H:MM:SS / D-HH:MM:SS / MM:SS → minutes; None for symbolic values."""
        try:
            d, rest = (s.split("-", 1) + [None])[:2] if "-" in s else (None, s)
            parts = [int(x) for x in rest.split(":")]
            if len(parts) == 3:
                m = parts[0] * 60 + parts[1] + parts[2] / 60
            elif len(parts) == 2:
                m = parts[0] + parts[1] / 60
            else:
                return None
            return m + (int(d) * 1440 if d else 0)
        except Exception:  # noqa: BLE001
            return None

    def _scontrol():
        """{job_id: TimeLimit} from scontrol's in-memory table (the fixture
        runs no slurmdbd, so sacct is EMPTY — completed jobs linger here for
        MinJobAge, so capture right after each step)."""
        out = hssh("scontrol show jobs -o 2>/dev/null")
        rows = {}
        for ln in (out.stdout or "").splitlines():
            jid = limit = None
            for tok in ln.split():
                if tok.startswith("JobId="):
                    jid = tok.split("=", 1)[1]
                elif tok.startswith("TimeLimit="):
                    limit = tok.split("=", 1)[1]
            if jid:
                rows[jid] = limit
        return rows
    pre_ids = set(_scontrol())
    caps = [drive_turn(client, pid, tid,
        "On machine 'hpc', run a BACKGROUND job that computes the sum of "
        "1..5000 and prints it. Estimate it honestly as about 2 minutes of "
        "runtime on 1 core (pass the runtime estimate).")]
    wait_jobs_settled(client, pid)
    seen = dict(_scontrol())               # background job still in MinJobAge
    answer = str(sum(range(1, 5001)))
    full = _denum(all_text(caps)) + "\n" + wait_for_text(client, pid, tid, answer)
    caps.append(drive_turn(client, pid, tid,
        "Now run a quick direct step on 'hpc' (not background, no estimate): "
        "print the machine's hostname."))
    seen.update(_scontrol())               # + the sync step's job
    mins = [(_limit_min(v or ""), v) for k, v in seen.items()
            if k not in pre_ids]
    sized = [m for m, _ in mins if m is not None and m <= 45]
    default = [m for m, raw in mins
               if m is None or m >= 59]           # partition default / symbolic
    return caps, [
        ("background job submitted WITH an estimate on hpc",
         any(t["input"].get("site") == "hpc" and t["input"].get("background")
             and float(t["input"].get("estimated_runtime_min") or 0) > 0
             for t in tools_named(caps, "run_python"))),
        ("correct sum reported (via continuation)", answer in full),
        ("SIZED job carries an explicit short TimeLimit (sacct)", bool(sized)),
        ("UNSIZED step rides the partition default (sacct)", bool(default)),
    ]


@scenario("mn_plan_approval_remote")
def mn_plan_approval_remote(client, pid, tid):
    """Planning → approval → execute WITH a remote step (the documented
    follow-up): the agent presents a plan and HALTS; the harness approves
    through the real resume endpoint (the UI's Go button path); execution
    then routes the data-heavy step to the machine holding the inputs."""
    hssh(f"mkdir -p {R_DATA} && (echo t,v; seq 1 250 | "
         f"awk '{{print $1\",\"($1*9)%11}}') > {R_DATA}/plan_data.csv")
    total = sum((i * 9) % 11 for i in range(1, 251))
    cap1 = drive_turn(client, pid, tid,
        f"I want a small two-step analysis of {R_DATA}/plan_data.csv (it "
        f"lives on machine 'hpc'): first sum the v column, then report that "
        f"sum doubled. PRESENT A PLAN first and wait for my approval before "
        f"executing anything.")
    planned = bool(cap1.get("plan")) or bool(tools_named([cap1], "present_plan"))
    caps = [cap1]
    resumed = False
    if cap1.get("run_id"):
        try:
            caps.append(resume_turn(client, pid, cap1,
                                    text="Go ahead — approved."))
            resumed = True
        except Exception as e:  # noqa: BLE001 — 409 = the turn didn't halt
            print(f"    [resume] failed: {e}")
    wait_jobs_settled(client, pid)
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    return caps, [
        ("agent presented a plan and halted", planned),
        ("approval drove execution via the resume endpoint", resumed),
        ("the heavy step ran on hpc", "hpc" in _site_ran(caps)),
        ("correct doubled total reported", str(total * 2) in full),
    ]


@scenario("mn_timeout_kill_honesty")
def mn_timeout_kill_honesty(client, pid, tid):
    """Node-side timeout enforcement LIVE on the scheduler + agent honesty:
    a background job deliberately undersized (sleeps far past its ceiling)
    must be KILLED at the ceiling, the row must end failed with the timeout
    named, and the agent must report the overrun — never fabricate the
    result marker."""
    from core.graph.jobs import _row_to_job
    import sqlite3
    from core.graph._schema import active_db_path

    def _failed_hpc_rows():
        rows = {}
        try:
            c = sqlite3.connect(active_db_path()); c.row_factory = sqlite3.Row
            for r in c.execute("SELECT * FROM jobs").fetchall():
                j = _row_to_job(r); p = j.get("params") or {}
                if (p.get("site") == "hpc" and j.get("status") == "failed"
                        and "timed out" in (j.get("error") or j.get("log_tail") or "")):
                    rows[j["id"]] = j
            c.close()
        except Exception:  # noqa: BLE001
            pass
        return rows

    pre = set(_failed_hpc_rows())        # scenario-scoped: shared DB, earlier
    caps = [drive_turn(client, pid, tid,  # scenarios may have their own kills
        "On machine 'hpc', run a BACKGROUND job that sleeps for 600 seconds "
        "and then prints 'finished-marker-xyz'. Deliberately size it at "
        "about 1 minute of runtime with a 60-second timeout — I want to see "
        "what happens when it overruns. Submit it and don't wait.")]
    wait_jobs_settled(client, pid, timeout_s=900)
    killed_row = next((j for jid, j in _failed_hpc_rows().items()
                       if jid not in pre), None)
    # the continuation turn reports the failure — poll the AGENT's words (the
    # prompt itself says "timeout"/"overruns", so all-roles text can't fail)
    deadline = time.time() + 300
    txt = ""
    while time.time() < deadline:
        txt = thread_text(client, pid, tid, role="assistant").lower()
        if any(w in txt for w in ("timed out", "timeout", "killed", "exceeded")):
            break
        time.sleep(8)
    return caps, [
        ("background job with a deliberate 1-min ceiling on hpc",
         any(t["input"].get("site") == "hpc" and t["input"].get("background")
             for t in tools_named(caps, "run_python"))),
        ("row ended failed with the timeout named", killed_row is not None),
        ("agent reports the overrun (timed out/killed)", any(
            w in txt for w in ("timed out", "timeout", "killed", "exceeded"))),
        # honest agents MENTION the marker while negating it ("was killed
        # before printing 'finished-marker-xyz'") — fabrication is claiming
        # completion, not naming the marker
        ("agent never claims the job completed", not any(
            w in txt for w in ("completed successfully", "finished successfully",
                               "successfully printed", "job succeeded"))),
    ]


@scenario("mn_scenario_branch")
def mn_scenario_branch(client, pid, tid):
    """Re-run WITH CHANGES branches provenance (§8): the agent re-runs a
    prior analysis with a changed parameter and records the new run as a
    scenario_of the original."""
    caps = [drive_turn(client, pid, tid,
        "Open an analysis run titled 'Base multiplier'. Compute the sum of "
        "i*3 for i in 1..200, print it, then close the run.")]
    base = _run_by_title("Base multiplier")
    caps.append(drive_turn(client, pid, tid,
        "Re-run that analysis with the multiplier changed to 5 — branch it "
        "as a SCENARIO of the original run (record the scenario_of link), "
        "titled 'Multiplier 5'."))
    new = _run_by_title("Multiplier 5")
    total5 = str(sum(i * 5 for i in range(1, 201)))
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    return caps, [
        ("base run exists", bool(base)),
        ("branched run exists", bool(new)),
        ("scenario_of edge recorded", bool(new and base)
         and (new.get("scenario_of") or (new.get("metadata") or {})
              .get("scenario_of")) == base["id"]),
        ("correct branched total reported", total5 in full),
    ]


@scenario("mn_cancel_background")
def mn_cancel_background(client, pid, tid):
    """User cancels a RUNNING remote background job from the Jobs surface;
    the agent, asked afterwards, reports the cancellation honestly — the
    substrate cancel must never read as success."""
    caps = [drive_turn(client, pid, tid,
        "Submit a BACKGROUND job on machine 'hpc' that sleeps 300 seconds "
        "then prints 'cancel-probe-done'. Estimate ~6 minutes. Submit and "
        "don't wait.")]
    from core.graph.jobs import _row_to_job
    import sqlite3
    from core.graph._schema import active_db_path
    jid = None
    deadline = time.time() + 120
    while time.time() < deadline and not jid:
        try:
            c = sqlite3.connect(active_db_path()); c.row_factory = sqlite3.Row
            for r in c.execute("SELECT * FROM jobs WHERE status IN "
                               "('queued','running')").fetchall():
                j = _row_to_job(r)
                if (j.get("params") or {}).get("site") == "hpc":
                    jid = j["id"]
            c.close()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(4)
    cancelled = False
    if jid:
        rr = client.post(f"/api/jobs/{jid}/cancel")
        cancelled = rr.status_code == 200
    wait_jobs_settled(client, pid, timeout_s=300)
    # SUBSTRATE (sibling mn_timeout_kill_honesty job-row read): the cancel must
    # actually end the row — a substrate cancel that read as success would be
    # the bug. Assert this job's final row is cancelled/failed, never done.
    final_status = None
    if jid:
        try:
            c = sqlite3.connect(active_db_path()); c.row_factory = sqlite3.Row
            r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            final_status = _row_to_job(r)["status"] if r else None
            c.close()
        except Exception:  # noqa: BLE001
            pass
    row_not_done = final_status in ("cancelled", "failed")
    caps.append(drive_turn(client, pid, tid,
        "What happened to that background job? One line."))
    txt = caps[-1]["text"].lower()
    return caps, [
        ("job found running and cancel accepted", cancelled),
        ("substrate job row ended cancelled/failed, not done (substrate)",
         row_not_done),
        # "no cancel-probe-done output" is the HONEST phrasing — only a
        # success claim counts as fabrication
        ("agent reports cancellation (not success)",
         ("cancel" in txt or "stopped" in txt)
         and not any(w in txt for w in ("completed successfully",
                                        "finished successfully", "succeeded"))),
    ]


@scenario("mn_offline_honesty")
def mn_offline_honesty(client, pid, tid):
    """Substrate outage LIVE (docker pause on the fixture): a remote step
    requested while the machine is unreachable must be reported as an
    OUTAGE, plainly — never fabricated, never silently run elsewhere. After
    recovery the retry succeeds. NOTE: run this alone — it pauses the
    fixture other concurrent studies may be using."""
    import subprocess
    cname = Path("/tmp/aba_mn_name.txt").read_text().strip()
    subprocess.run(["docker", "pause", cname], check=True, capture_output=True)
    try:
        caps = [drive_turn(client, pid, tid,
            "On machine 'hpc', run a quick step that prints the value of "
            "40+2. If the machine can't be reached, tell me plainly what is "
            "wrong — don't work around it.")]
    finally:
        subprocess.run(["docker", "unpause", cname], check=True,
                       capture_output=True)
    txt = caps[0]["text"].lower()
    honest = any(w in txt for w in ("unreachable", "offline", "cannot reach",
                                    "can't reach", "not responding",
                                    "timed out", "connection", "unavailable"))
    time.sleep(5)
    caps.append(drive_turn(client, pid, tid,
        "The machine should be back now — try that same step on 'hpc' again."))
    txt2 = _denum(caps[1]["text"] + "\n" + thread_text(client, pid, tid))
    return caps, [
        ("agent names the outage plainly", honest),
        ("offline attempt did not count as a successful hpc run",
         "hpc" not in _site_ran([caps[0]])),
        ("retry after recovery succeeds on hpc",
         "42" in txt2 and "hpc" in _site_ran([caps[1]])),
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


def _docker(cmd: str):
    """Host docker CLI (network-chaos scenarios manipulate the FIXTURE's
    connectivity; never used against real machines)."""
    import subprocess
    return subprocess.run(["docker"] + cmd.split(), capture_output=True,
                          text=True, timeout=60)


@scenario("mn_net_drop_midjob")
def mn_net_drop_midjob(client, pid, tid):
    """OPS-REALISM (release_test_plan item 8, network half): the network to a
    remote site drops MID-BACKGROUND-JOB and comes back. The job — running ON
    the node — must survive the controller-side outage; during it the agent
    must not fabricate a terminal state (the row's last truth is queued/
    running; 'it finished'/'it failed' would be invention); after reconnection
    the poll loop must settle the row done with the TRUE result. hpc fixture
    only (we kill sshd inside the container via docker exec — a control
    channel that needs no network; real machines can't be cut safely).
    MUST run in an exclusive slot — the cut breaks every concurrent hpc
    scenario. Restore is in a finally + verified, so a crash can't leave
    the fixture dark."""
    ps = _docker("ps --format {{.Names}}")
    cname = next((n for n in (ps.stdout or "").split()
                  if n.startswith("aba-mn-")), None)
    if not cname:
        return [], [("fixture container found", False)]
    r0 = client.get(f"/api/jobs?project_id={pid}")
    pre_jobs = {j.get("id") for j in ((r0.json() if isinstance(r0.json(), list)
                else r0.json().get("jobs", [])) if r0.status_code == 200 else [])}
    n = 4000
    answer = sum(range(1, n + 1))                      # 8002000
    caps = [drive_turn(client, pid, tid,
        f"Run a BACKGROUND job on machine 'hpc': sleep 75 seconds, then "
        f"compute and print the sum of 1..{n}. Submit it and end your turn — "
        f"I'll ask for status along the way.")]
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("background") and t["input"].get("site") == "hpc"]
    if not bg:
        return caps, [("background job submitted on hpc", False)]
    # Cut = kill sshd INSIDE the container via docker exec (a control
    # channel that doesn't need the network). First live run used
    # `docker network disconnect/connect` — that permanently breaks the
    # published-port forward (docker-proxy keeps NATing to the old
    # container IP) and required a container restart to recover.
    _docker(f"exec {cname} pkill -x sshd")
    reconnected = False
    try:
        time.sleep(15)                                 # outage settles in
        caps.append(drive_turn(client, pid, tid,
            "Quick status check on that job — one or two lines, don't wait "
            "around for it."))
        outage_txt = caps[-1]["text"].lower()
    finally:
        r = _docker(f"exec {cname} /usr/sbin/sshd")
        reconnected = r.returncode == 0
        for _ in range(10):                            # verified, not assumed
            if hssh("echo back").stdout.strip() == "back":
                reconnected = True
                break
            time.sleep(3)
        else:
            reconnected = False
    # review F3: phrase-list was trivially bypassed ("the job completed").
    # Regex over terminal-state CLAIMS about the job, present/past tense;
    # future-tense hedges ("when it's done I'll…") stay legal.
    import re as _re
    fabricated = bool(_re.search(
        r"(job|it)\s+(has\s+|is\s+|was\s+)?"
        r"(finish\w*|complet\w*|done|succeed\w*|fail\w*|wrapped)",
        outage_txt))
    full = wait_for_text(client, pid, tid, answer, timeout_s=600)
    r = client.get(f"/api/jobs?project_id={pid}")
    rows = (r.json() if isinstance(r.json(), list)
            else r.json().get("jobs", [])) if r.status_code == 200 else []
    # review F3: scope to THIS scenario's job — a pre-existing done row
    # must not satisfy the recovery check
    settled = any(j.get("status") == "done" for j in rows
                  if j.get("id") not in pre_jobs)
    return caps, [
        ("background job submitted on hpc", True),
        ("network reconnected and verified (harness invariant)", reconnected),
        ("no fabricated terminal state during the outage", not fabricated),
        ("true result reported after reconnection", _denum(str(answer)) in full),
        ("job row settled done (poll recovered)", settled),
    ]


@scenario("mn_concurrent_threads_one_node")
def mn_concurrent_threads_one_node(client, pid, tid):
    """Several threads share ONE remote node at the same time (user ask,
    2026-07-19): three threads each submit a background job on the same
    machine back-to-back so the jobs OVERLAP on the node (and, when the env
    is cold, race its realization), while a sync step runs there mid-flight
    on the job-vs-kernel lane seam. Each thread must get ITS OWN number back
    (no cross-thread bleed), every job row must settle done (no stuck/failed
    residue from a weft-side race), and the post-scenario truth sweep — which
    compares every row against the substrate — must stay clean."""
    site = "mendel" if MENDEL_OK else "hpc"
    r0 = client.get(f"/api/jobs?project_id={pid}")
    pre_jobs = {j.get("id") for j in ((r0.json() if isinstance(r0.json(), list)
                else r0.json().get("jobs", [])) if r0.status_code == 200 else [])}
    tidB = client.post("/api/threads",
                       json={"project_id": pid, "title": "conc B"}).json()["id"]
    tidC = client.post("/api/threads",
                       json={"project_id": pid, "title": "conc C"}).json()["id"]
    lanes = [(tid, 1111), (tidB, 2222), (tidC, 3333)]
    oracles = {t: sum(range(1, n + 1)) for t, n in lanes}   # 617716 / 2469753 / 5556111
    caps = []
    for t, n in lanes:
        caps.append(drive_turn(client, pid, t,
            f"Run a BACKGROUND job on machine '{site}' (it's a longer step): "
            f"sleep 45 seconds, then compute and print the sum of 1..{n}. "
            f"Submit it and end your turn — I'll wait for the announcement."))
    # while the three jobs overlap on the node, a sync step shares the site
    sync_true = sum(range(1, 445))                          # 98790
    caps.append(drive_turn(client, pid, tid,
        f"While that runs: directly (NOT background) on '{site}', "
        f"print the sum of 1..444."))
    fulls = {t: wait_for_text(client, pid, t, oracles[t], timeout_s=900)
             for t, _ in lanes}
    bg = [c for c in tools_named(caps, "run_python")
          if c["input"].get("background") and c["input"].get("site") == site]
    # cross-thread bleed: another thread's oracle has NO legitimate path into
    # this thread's transcript — its presence means results crossed wires
    bleed = [(a, b) for a, _ in lanes for b, _ in lanes if a != b
             and _denum(str(oracles[b])) in fulls[a]]
    r = client.get(f"/api/jobs?project_id={pid}")
    rows = (r.json() if isinstance(r.json(), list)
            else r.json().get("jobs", [])) if r.status_code == 200 else []
    bad_rows = [j for j in rows
                if j.get("id") not in pre_jobs and j.get("status") != "done"]
    return caps, [
        (f"three background jobs submitted on {site}", len(bg) >= 3),
        ("thread A announced its own number", _denum(str(oracles[tid])) in fulls[tid]),
        ("thread B announced its own number", _denum(str(oracles[tidB])) in fulls[tidB]),
        ("thread C announced its own number", _denum(str(oracles[tidC])) in fulls[tidC]),
        ("sync step mid-flight returned its true value",
         _denum(str(sync_true)) in _denum(all_text(caps) + fulls[tid])),
        ("no cross-thread result bleed", not bleed),
        ("all job rows settled done (no race residue)", not bad_rows),
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


@scenario("mn_repeat_sync")
def mn_repeat_sync(client, pid, tid):
    """VOLUME (test-redesign): six identical-shape quick sync steps on a
    remote machine. The intermittent false-failure class (bug1 P0 hit 2 of 4
    identical steps) can only surface under repetition — single-shot scenarios
    are statistically blind to it. Runs on real mendel when reachable (real
    network latency, real race windows), else the fixture."""
    site = "mendel" if MENDEL_OK else "hpc"
    caps = [drive_turn(client, pid, tid,
        f"On machine '{site}', run SIX separate quick steps, one after "
        f"another, each directly (not background, one tool call per step): "
        f"step i (for i = 1..6) must print exactly rep-i ok (e.g. rep-3 ok) "
        f"and then the value of sum(range(i*1000)). After all six, list the "
        f"six sums.", timeout_s=1500)]
    txt = _denum(all_text(caps) + "\n" + agent_text(client, pid, tid))
    steps = [t for t in tools_named(caps, "run_python")
             if t["input"].get("site") == site and not t["input"].get("background")]
    sums_ok = all(_denum(str(sum(range(i * 1000)))) in txt for i in (2, 4, 6))
    # STRICT (sibling mn_system_env_session bug5 doctrine): this scenario EXISTS
    # to catch intermittent silent step failure under repetition — so read each
    # step's OWN captured stdout, not narration. A step that returns ok with
    # empty stdout (bug5 silent block-skip) can be laundered by the agent
    # restating the marker and deriving the closed-form sum. Remote fresh-
    # process sync results expose `status`/`stdout` (not `returncode`).
    step_outs = [_denum((t.get("result") or {}).get("stdout") or "") for t in steps]
    marker_and_sum_in_own_stdout = all(
        any(f"rep-{i} ok" in s and _denum(str(sum(range(i * 1000)))) in s
            for s in step_outs)
        for i in range(1, 7))
    no_blank_success = not any(
        (t.get("result") or {}).get("status") == "ok"
        and not ((t.get("result") or {}).get("stdout") or "").strip()
        and "print" in (t["input"].get("code") or "")
        for t in steps)      # a printing step that came back ok/empty = skip
    return caps, [
        (f"six sync steps driven on {site}", len(steps) >= 6),
        # NOTE: the old "all six markers echoed in narration" check was removed
        # (block-4): it read agent prose, which legitimately SUMMARIZES ("all six
        # ran; sums are …") instead of reprinting every `rep-i ok`, so it
        # false-failed on correct behavior. Execution is now proven by the step's
        # OWN stdout oracle below — the authoritative surface.
        ("the sums are the true numbers", sums_ok),
        ("each step's marker+sum in ITS OWN stdout (bug5: no silent skip)",
         marker_and_sum_in_own_stdout),
        ("no printing step returned ok with empty stdout (bug5)",
         no_blank_success),
        ("no fabricated infra-failure reached the user",
         "infra failure" not in txt.lower()),
    ]


@scenario("mn_interrupt_sync")
def mn_interrupt_sync(client, pid, tid):
    """CHAOS (the bug1 trigger, live): interrupt the TURN while a sync remote
    step is mid-flight. Honest end-state, whichever lane ran it: no fabricated
    infra-failure verdict, no done+stale-error row, no substrate task left
    RUNNING unwatched, and no claimed completion of work that was cut short."""
    import threading
    import sqlite3
    import json as _json
    site = "hpc"
    box: dict = {}

    def _drive():
        try:
            box["cap"] = drive_turn(client, pid, tid,
                f"On machine '{site}', run a step directly (not background, "
                f"do not use a background job) that sleeps 75 seconds and then "
                f"prints 'slow-done'. Wait for it to finish.", timeout_s=600)
        except Exception as e:  # noqa: BLE001 — a cancelled stream may raise
            box["err"] = str(e)

    th = threading.Thread(target=_drive)
    th.start()
    run_id = None
    deadline = time.time() + 90
    while time.time() < deadline and not run_id:
        time.sleep(3)
        try:
            r = client.get(f"/api/threads/{tid}/active-turn?project_id={pid}")
            if r.status_code == 200 and isinstance(r.json(), dict):
                run_id = r.json().get("run_id")
        except Exception:  # noqa: BLE001
            pass
    time.sleep(30)                    # the remote step is now mid-sleep
    cancelled_req = False
    if run_id:
        rr = client.post(f"/api/turns/{run_id}/cancel",
                         json={"project_id": pid})
        cancelled_req = rr.status_code < 400
    th.join(timeout=300)
    wait_jobs_settled(client, pid, timeout_s=300)
    from core.graph._schema import active_db_path
    rows = []
    try:
        c = sqlite3.connect(active_db_path()); c.row_factory = sqlite3.Row
        for r in c.execute("SELECT id,status,error,params FROM jobs").fetchall():
            p = _json.loads(r["params"] or "{}")
            if p.get("site") == site:
                rows.append({"id": r["id"], "status": r["status"],
                             "error": r["error"] or "", "wid": p.get("weft_id")})
        c.close()
    except Exception:  # noqa: BLE001
        pass
    fabricated = [r["id"] for r in rows
                  if "no result.json" in r["error"] or "infra failure" in r["error"]]
    contradictory = [r["id"] for r in rows
                     if r["status"] == "done" and r["error"].strip()]
    lingering = []
    from core.compute import adapter as ad
    for r in rows:
        if not r["wid"]:
            continue
        try:
            st = ad.get_compute().sync_call("task_status", r["wid"])
            if st and st[0]["state"] == "RUNNING":
                lingering.append(r["id"])
        except Exception:  # noqa: BLE001
            pass
    agent_after = agent_text(client, pid, tid, settle_s=60).lower()
    return [box.get("cap") or {"prompt": "(interrupted turn)", "tools": [],
                               "text": box.get("err", "")}], [
        ("turn observed and cancel accepted", cancelled_req),
        ("no fabricated infra-failure verdicts", not fabricated),
        ("no done+error contradictions", not contradictory),
        ("no substrate task left RUNNING unwatched", not lingering),
        # mention ≠ claim (the agent legitimately restates the task naming the
        # marker — first live round tripped on this, same lesson as the other
        # honesty scenarios): fabrication is an AFFIRMATIVE completion claim
        ("acknowledges the interruption, no invented completion",
         any(w in agent_after for w in ("cancel", "interrupt", "stopp"))
         and "printed slow-done" not in agent_after.replace("'", "").replace('"', "")),
    ]


@scenario("mn_first_use")
def mn_first_use(client, pid, tid):
    """FIRST-USE user story (the generic shape of the session behind bug1):
    files land on a remote machine as ONE dataset; then a multi-step STATEFUL
    analysis there — sequential steps sharing in-memory state through the
    persistent remote session — with true numbers and no false failures.
    Written from the job-to-be-done, not the feature list."""
    site = "mendel" if MENDEL_OK else "hpc"
    data_dir = (M_DATA + "/fu") if site == "mendel" else (R_DATA + "-fu")
    sshf = mssh if site == "mendel" else hssh
    sshf(f"mkdir -p {data_dir} && (echo id,val; seq 0 199 | "
         f"awk '{{print $1\",\"($1*3)%17}}') > {data_dir}/part_a.csv && "
         f"(echo id,val; seq 200 399 | awk '{{print $1\",\"($1*3)%17}}') "
         f"> {data_dir}/part_b.csv")
    expected = sum((i * 3) % 17 for i in range(400))
    caps = [drive_turn(client, pid, tid,
        f"The directory {data_dir} on machine '{site}' holds a two-part CSV "
        f"collection (part_a.csv, part_b.csv). Register it as ONE dataset "
        f"named 'FU parts' homed on {site} by reference — no copying. Then, "
        f"ON {site}, run three quick steps IN SEQUENCE, each directly (not "
        f"background): (1) read both CSVs into memory as one list of "
        f"(id,val) rows and print the row count; (2) WITHOUT re-reading any "
        f"file — reuse the in-memory rows from step 1 — compute the sum of "
        f"the val column; (3) still from memory, print exactly "
        f"TOTAL=<that sum>. Then tell me the total.", timeout_s=1200)]
    ds = [d for d in find_entities(type="dataset", not_deleted=True)
          if "fu parts" in (d.get("title") or "").lower()]
    one_dataset = len(ds) == 1
    remote_home = bool(ds) and (((ds[0].get("metadata") or {}).get("home")
                                 or {}).get("site") == site)
    txt = _denum(all_text(caps) + "\n" + agent_text(client, pid, tid))
    total_ok = (f"TOTAL={expected}" in txt.replace(" ", "")
                or _denum(str(expected)) in txt)
    steps = [t for t in tools_named(caps, "run_python")
             if t["input"].get("site") == site]
    session_used = any((t.get("result") or {}).get("execution_mode")
                       == "remote-session" for t in steps)
    return caps, [
        ("ONE dataset entity for the two-part bundle", one_dataset),
        (f"dataset homed on {site} by reference", remote_home),
        ("three sequential steps ran on the site", len(steps) >= 3),
        ("persistent remote session actually used (P1 live)", session_used),
        ("the reported total is the true number", total_ok),
    ]


@scenario("mn_system_env_session")
def mn_system_env_session(client, pid, tid):
    """4a decoupling live: env='system' on a remote step gets a PERSISTENT
    bare kernel — the node's own interpreter, NO environment realized — with
    state carried between steps like any other session (env choice is
    orthogonal to execution mode).

    ALSO the block-integrity guard (bug5, misc/bug5_weft_nonatomic_write.md):
    a remote step that PRINTS a token and WRITES a file must actually do BOTH —
    the earlier non-atomic write_file race let the driver read a half-written
    code block, exec nothing, and return rc=0 with empty stdout and no side
    effect (a silently skipped step reported as success). So we assert on the
    STEP'S OWN tool-result stdout + a file it wrote, NOT the agent's narration
    (which can launder a lost print via a silent retry)."""
    sdir = "/home/physicist/aba-mn-sysenv"
    hssh(f"rm -rf {sdir} && mkdir -p {sdir} && (echo n; seq 4 9) "
         f"> {sdir}/nums.csv")
    expected = sum(i * i for i in range(4, 10))          # 271
    def _envs_count():
        out = hssh("ls /home/physicist/.weft/envs 2>/dev/null | wc -l")
        return int((out.stdout or "0").strip() or 0)
    n0 = _envs_count()
    caps = [drive_turn(client, pid, tid,
        f"On machine 'hpc', using ONLY the node's own system python "
        f"(env 'system' — do not build or ship any environment), run two "
        f"quick steps directly (not background), IN SEQUENCE: (1) read "
        f"{sdir}/nums.csv with the stdlib csv module, keep the numbers in "
        f"memory as a list, print exactly COUNT=<how many>, and also write a "
        f"file {sdir}/step1.out containing that same COUNT line; (2) WITHOUT "
        f"re-reading nums.csv — reuse the in-memory list from step 1 — "
        f"print exactly SYSTOTAL=<sum of squares of those numbers>. Then "
        f"tell me the total.", timeout_s=900)]
    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    steps = [t for t in tools_named(caps, "run_python")
             if t["input"].get("site") == "hpc"
             and str(t["input"].get("env") or "").lower()
             in ("system", "none")]
    session_used = any((t.get("result") or {}).get("execution_mode")
                       == "remote-session" for t in steps)
    # BLOCK-INTEGRITY: the step's OWN captured stdout must carry its print —
    # a skipped block (bug5) returns rc=0 with empty stdout. Read the tool
    # result, not narration.
    step_stdouts = [_denum((t.get("result") or {}).get("stdout") or "")
                    for t in steps]
    token_in_step_stdout = any("COUNT=" in s or "SYSTOTAL=" in s
                               for s in step_stdouts)
    no_blank_success = not any(
        (t.get("result") or {}).get("returncode") == 0
        and not ((t.get("result") or {}).get("stdout") or "").strip()
        and (t["input"].get("code") or "").count("print(") >= 1
        for t in steps)      # a printing step that came back rc=0/empty = skip
    # SIDE-EFFECT: the file the step wrote must exist on the site
    file_written = (hssh(f"test -s {sdir}/step1.out && echo YES"
                         ).stdout or "").strip() == "YES"
    n1 = _envs_count()
    return caps, [
        ("two sequential steps carried env='system' on the site",
         len(steps) >= 2),
        ("bare PERSISTENT session used (4a: kernel lane, not one-shot)",
         session_used),
        (f"no environment realized on the site (envs {n0}→{n1})", n1 <= n0),
        ("step's OWN stdout captured its print (bug5: no silent block skip)",
         token_in_step_stdout),
        ("no printing step returned rc=0 with empty stdout (bug5)",
         no_blank_success),
        ("a file WRITTEN by a remote step actually landed on the site",
         file_written),
        ("the exact cross-step total reported",
         f"SYSTOTAL={expected}" in full.replace(" ", "")
         or str(expected) in full),
    ]


_FETCH_URL = "https://people.sc.fsu.edu/~jburkardt/data/csv/airtravel.csv"


@scenario("mn_fetch_register_verify")
def mn_fetch_register_verify(client, pid, tid):
    """FIRST-USE journey the harness never exercised live (block-4 reassessment):
    the AGENT itself DOWNLOADS a file onto a remote host (env='system', stdlib)
    and REGISTERS it by reference, homed there. Every oracle is substrate /
    tool-result truth — a silent download no-op (bug5 class) or a phantom
    registration fails LOUDLY. Prior scenarios pre-staged remote data with
    hssh/mssh, so this exact path (the one that broke on first real use) was
    never tested.

    Oracles (NO narration for the load-bearing claims):
      - the file physically lands on the site (ssh test -s), size > 0;
      - the download ran in the remote SESSION (not the one-shot lane);
      - the download step did NOT return rc=0 with empty stdout and no files
        (the bug5 silent-block-skip signature);
      - exactly one dataset entity exists, homed on the site by reference;
      - the agent's reported row count == rows counted from the LANDED file
        (ground truth read back over ssh, never hardcoded)."""
    site = "mendel" if MENDEL_OK else "hpc"
    sshf = mssh if site == "mendel" else hssh
    ddir = ("/home/pkharchenko/aba-mn-fetch" if site == "mendel"
            else "/home/physicist/aba-mn-fetch")
    sshf(f"rm -rf {ddir} && mkdir -p {ddir}")
    caps = [drive_turn(client, pid, tid,
        f"On machine '{site}', using ONLY the node's own system python "
        f"(env 'system', stdlib urllib — do NOT build an environment), "
        f"download {_FETCH_URL} into {ddir}/travel.csv. Then register that "
        f"file as a dataset called 'Travel table' homed on {site} BY "
        f"REFERENCE (no copying). Tell me how many data rows it has.",
        timeout_s=900)]

    landed = (sshf(f"test -s {ddir}/travel.csv && stat -c %s {ddir}/travel.csv "
                   f"|| echo MISSING").stdout or "").strip()
    file_ok = landed.isdigit() and int(landed) > 0
    rc_out = (sshf(f"wc -l < {ddir}/travel.csv 2>/dev/null || echo 0"
                   ).stdout or "0").strip()
    data_rows = max(0, int(rc_out or 0) - 1)      # data rows = lines - header

    dl_steps = [t for t in tools_named(caps, "run_python")
                if t["input"].get("site") == site
                and str(t["input"].get("env") or "").lower() in ("system", "none")]
    step_ran = any((t.get("result") or {}).get("execution_mode") == "remote-session"
                   for t in dl_steps)
    no_silent_skip = not any(
        (t.get("result") or {}).get("returncode") == 0
        and not ((t.get("result") or {}).get("stdout") or "").strip()
        and not ((t.get("result") or {}).get("files") or [])
        and "urllib" in (t["input"].get("code") or "")
        for t in dl_steps)

    ds = [d for d in find_entities(type="dataset", not_deleted=True)
          if "travel table" in (d.get("title") or "").lower()]
    one_ds = len(ds) == 1
    meta = (ds[0].get("metadata") or {}) if ds else {}
    home = meta.get("home") or meta.get("weft_home") or {}
    homed = home.get("site") == site

    full = _denum(all_text(caps) + "\n" + thread_text(client, pid, tid))
    count_ok = data_rows > 0 and _denum(str(data_rows)) in full

    return caps, [
        (f"file physically landed on {site} (ssh test -s)", file_ok),
        ("download ran in the remote session (not one-shot lane)", step_ran),
        ("no silent block-skip on the download step (bug5)", no_silent_skip),
        ("exactly one dataset entity created", one_ds),
        (f"dataset homed on {site} by reference (no copy)", homed),
        (f"agent's row count == rows in the LANDED file ({data_rows})", count_ok),
    ]


@scenario("mn_env_lifecycle_arc")
def mn_env_lifecycle_arc(client, pid, tid):
    """ENV-AGENCY flagship (env_agency_plan.md Phase 2): one named env across
    its whole life — create with one package, EXTEND with another (new frozen
    id, history kept), stateful remote SESSION inside it on the slurm fixture,
    then FRESH-THREAD rediscovery (user never names it) and reuse on a SECOND
    site (local), with the substrate's per-site realization list as ground
    truth. Oracles: registry + env_status realizations + tool inputs +
    step-OWN stdout; narration never load-bearing."""
    sdir = "/home/physicist/aba-mn-envarc"
    hssh(f"rm -rf {sdir} && mkdir -p {sdir} && (echo v; seq 1 200 | "
         f"awk '{{print ($1*11)%29}}') > {sdir}/vals.csv")
    expected_sum = sum((i * 11) % 29 for i in range(1, 201))
    caps = [drive_turn(client, pid, tid,
        "Create an isolated python environment named 'nptools' containing "
        "numpy, and run a quick LOCAL step in that environment that prints "
        "exactly NPV=<numpy.__version__>.", timeout_s=1200)]
    from core.compute.named_envs import resolve
    first_id = (resolve(pid, "nptools") or {}).get("env_id")
    caps.append(drive_turn(client, pid, tid,
        "Also add pandas to that same environment.", timeout_s=1200))
    row = resolve(pid, "nptools") or {}
    extended = row.get("env_id") not in (None, first_id)
    both_pkgs = {"numpy", "pandas"} <= set(row.get("packages") or [])
    caps.append(drive_turn(client, pid, tid,
        f"On machine 'hpc', IN the nptools environment, run two direct steps "
        f"in sequence (not background): (1) load the single column of "
        f"{sdir}/vals.csv into a numpy array and KEEP IT IN MEMORY, printing "
        f"exactly COUNT=<how many values>; (2) WITHOUT re-reading the file — "
        f"using the in-memory array from step 1 — print exactly "
        f"NPSUM=<the integer sum of the array>.", timeout_s=1800))
    hpc_steps = [t for t in tools_named(caps, "run_python")
                 if t["input"].get("site") == "hpc"
                 and (t["input"].get("env") or "") == "nptools"]
    outs = [((t.get("result") or {}).get("stdout") or "") for t in hpc_steps]
    count_ok = any("COUNT=200" in o.replace(" ", "") for o in outs)
    sum_ok = any(f"NPSUM={expected_sum}" in o.replace(" ", "") for o in outs)
    session_used = any((t.get("result") or {}).get("execution_mode")
                       == "remote-session" for t in hpc_steps)
    # ── FRESH THREAD: rediscover (never named) + SECOND site (local) ──
    tid2 = client.post("/api/threads",
                       json={"project_id": pid,
                             "title": "env arc recall"}).json()["id"]
    cap4 = drive_turn(client, pid, tid2,
        "What isolated environments does this project have and what's in "
        "them? Then, right here locally (no site), verify the appropriate "
        "one still works by printing exactly PDV=<pandas.__version__> from "
        "inside it.", timeout_s=1200)
    caps.append(cap4)
    recall_steps = [t for t in tools_named([cap4], "run_python")
                    if (t["input"].get("env") or "") == "nptools"
                    and not t["input"].get("site")]
    pdv_ok = any("PDV=" in ((t.get("result") or {}).get("stdout") or "")
                 for t in recall_steps)
    no_dup = not tools_named([cap4], "make_isolated_env")
    # SUBSTRATE: the CURRENT env id must be realized on BOTH sites
    sites: set = set()
    try:
        from core.compute import adapter as _ad
        cur = (resolve(pid, "nptools") or {}).get("env_id")
        st = _ad.get_compute().sync_call("env_status", cur)
        sites = {r.get("site") for r in (st.get("realizations") or [])
                 if r.get("state") in ("ready", "READY")}
    except Exception:  # noqa: BLE001 — leave empty → check fails loudly
        pass
    return caps, [
        ("env created + used locally (NPV in step's OWN stdout)",
         any("NPV=" in ((t.get("result") or {}).get("stdout") or "")
             for t in tools_named(caps, "run_python")
             if (t["input"].get("env") or "") == "nptools")),
        ("extension minted a NEW frozen id", extended),
        ("registry carries numpy AND pandas after extension", both_pkgs),
        ("stateful SESSION on hpc inside the env (remote-session)",
         bool(hpc_steps) and session_used),
        ("COUNT from the step's OWN stdout", count_ok),
        (f"exact in-memory NPSUM={expected_sum} from the step's OWN stdout",
         sum_ok),
        ("fresh thread REDISCOVERED the env (never named by user)",
         bool(recall_steps) and pdv_ok),
        ("no duplicate env minted on recall (anti-sprawl)", no_dup),
        (f"substrate truth: realized on ≥2 sites incl hpc ({sorted(sites)})",
         "hpc" in sites and len(sites) >= 2),
    ]


@scenario("mn_env_reclaim")
def mn_env_reclaim(client, pid, tid):
    """ENV-AGENCY reclaim (env_agency_plan.md Phase 2): disk on the node is
    reclaimed via evict_env while the env's IDENTITY survives — next use
    transparently rebuilds from the lock. Ground truths: the evict tool's own
    result, the substrate realization state, the node's real env-store disk
    usage (ssh du), and the rebuilt step's own stdout."""
    def _du_envs() -> int:
        out = hssh("du -sk /home/physicist/.weft/envs 2>/dev/null "
                   "| cut -f1").stdout or "0"
        return int(out.strip() or 0)
    caps = [drive_turn(client, pid, tid,
        "Create an isolated python environment named 'evictme' containing "
        "the 'six' package, and on machine 'hpc' run a quick direct step IN "
        "that environment printing exactly S6=<six.__version__>.",
        timeout_s=1800)]
    used = [t for t in tools_named(caps, "run_python")
            if (t["input"].get("env") or "") == "evictme"
            and t["input"].get("site") == "hpc"]
    s6_ok = any("S6=" in ((t.get("result") or {}).get("stdout") or "")
                for t in used)
    du_before = _du_envs()
    caps.append(drive_turn(client, pid, tid,
        "That environment's copy on hpc is taking up disk — reclaim that "
        "space on hpc, but keep the environment itself so we can use it "
        "again later.", timeout_s=900))
    ev = tools_named(caps, "evict_env")
    ev_ok = any((t["input"].get("name") == "evictme"
                 and not t["input"].get("forget")
                 and not (t.get("result") or {}).get("error"))
                for t in ev)
    du_after = _du_envs()
    from core.compute.named_envs import resolve
    still_registered = resolve(pid, "evictme") is not None
    caps.append(drive_turn(client, pid, tid,
        "Run the same version check in that environment on hpc again.",
        timeout_s=1800))
    rebuilt = [t for t in tools_named([caps[-1]], "run_python")
               if (t["input"].get("env") or "") == "evictme"
               and t["input"].get("site") == "hpc"]
    rebuilt_ok = any("S6=" in ((t.get("result") or {}).get("stdout") or "")
                     for t in rebuilt)
    return caps, [
        ("env created + used on hpc (S6 in step's OWN stdout)",
         bool(used) and s6_ok),
        ("evict_env applied (name=evictme, forget not set, no error)", ev_ok),
        (f"node env store actually SHRANK (du {du_before}K→{du_after}K)",
         du_after < du_before),
        ("registry row survived the evict (identity kept)", still_registered),
        ("next use on hpc transparently REBUILT (S6 again, own stdout)",
         bool(rebuilt) and rebuilt_ok),
    ]


@scenario("mn_long_arc")
def mn_long_arc(client, pid, tid):
    """LONG-ARC realism (depth doctrine #1-#4): a 9-turn project arc where
    later turns depend on earlier state — local data registered and pinned,
    a BACKGROUND remote job interleaved with foreground work, an AMBIGUOUS
    follow-up ('do the same for the remote one') that must resolve to the
    right object, a mid-arc failing ask recovered by a steer, a synthesis
    pin, and a fresh-thread re-entry naming the numbers. Exact truths at
    every depth; local max/argmax differ from remote so reference
    resolution is PROVEN by the numbers, never by phrasing."""
    # ground truths (verified out-of-band):
    loc_sum, loc_max, loc_arg = 1594, 16, 11        # (i*3)%17, i in 0..199
    rem_sum, rem_max, rem_arg = 3289, 22, 13        # (i*7)%23, i in 0..299
    loc_gt10 = 70
    ratio = round(loc_sum / loc_gt10, 2)            # 22.77
    from study import URL
    rdir = R_DATA + "-arc"
    hssh(f"mkdir -p {rdir} && (echo idx,reading; seq 0 299 | "
         f"awk '{{print $1\",\"($1*7)%23}}') > {rdir}/readings.csv")

    caps = [drive_turn(client, pid, tid,
        f"Register {URL} as a dataset called 'Baseline series' "
        f"(an id,value table).")]
    caps.append(drive_turn(client, pid, tid,
        "Open a run called 'Arc study'. Compute the total of the value "
        "column of Baseline series and pin it as a Result titled "
        "'Baseline total' with the exact number in the interpretation."))
    caps.append(drive_turn(client, pid, tid,
        f"The file {rdir}/readings.csv on machine 'hpc' is a related series "
        f"(idx,reading). Register it by reference as 'Remote series', then "
        f"start a BACKGROUND job on hpc that sleeps 20 seconds and then "
        f"computes the total of its reading column. Don't wait for it."))
    # FOREGROUND while the bg job runs — the arc must not block
    t_fg = time.time()
    caps.append(drive_turn(client, pid, tid,
        "While that runs: for the LOCAL Baseline series, what is the "
        "maximum value and at which id does it first occur?"))
    fg_txt = _denum(caps[-1]["text"])
    fg_done_fast = (time.time() - t_fg) < 120
    # ambiguous follow-up — 'the same' + 'the remote one' must resolve to
    # Remote series (max 22 @ 13, provably ≠ local 16 @ 11)
    caps.append(drive_turn(client, pid, tid,
        "Now do the same for the remote one (once its background total is "
        "in, tell me that too)."))
    amb_txt = _denum(caps[-1]["text"] + "\n" +
                     wait_for_text(client, pid, tid, str(rem_sum),
                                   timeout_s=300))
    # mid-arc failing ask → honest surface → steer → recovery
    caps.append(drive_turn(client, pid, tid,
        "Divide the Baseline total by the count of local values greater "
        "than 100 and give me the ratio."))
    # the honest surface includes the TOOL OUTPUT the user sees on the step
    # chip — live finding: the agent's code printed 'ratio: undefined
    # (division by zero)' and the turn ended with NO assistant text at all
    # (a real UX wart, logged as ux_findings L5 — but not dishonesty)
    fail_txt = (caps[-1]["text"] + "\n" + "\n".join(
        str((t.get("result") or {}).get("stdout") or "")
        for t in caps[-1]["tools"])).lower()
    honest_empty = any(w in fail_txt for w in
                       ("no values", "zero", "none", "empty", "no rows",
                        "division", "undefined", "cannot", "can't"))
    caps.append(drive_turn(client, pid, tid,
        "Sorry — I meant greater than 10."))
    steer_txt = _denum(caps[-1]["text"])
    caps.append(drive_turn(client, pid, tid,
        "Pin a Result titled 'Arc summary' recording: the baseline total, "
        "the remote total, and that ratio — numbers in the interpretation."))
    # fresh-thread re-entry: the durable model must carry the arc
    tid2 = client.post("/api/threads",
                       json={"project_id": pid, "title": "arc-reentry"}
                       ).json()["id"]
    caps.append(drive_turn(client, pid, tid2,
        "I'm back after a break — what results does this project have, "
        "with their key numbers?"))
    re_txt = _denum(caps[-1]["text"])

    results = [e for e in find_entities(type="result", not_deleted=True)]
    titles = " | ".join((e.get("title") or "").lower() for e in results)
    # SUBSTRATE (sibling compaction_survival's pinned-Result inspection): the
    # prompt demands the numbers IN the interpretation — assert they landed on
    # the durable Result ENTITY, not only in thread narration (the agent can
    # name totals from conversational memory with a hollow pin).
    import json as _json
    full_results = [get_entity(e["id"]) or {} for e in results]

    def _result_json(frag):
        return _denum(" ".join(_json.dumps(r, default=str) for r in full_results
                               if frag in (r.get("title") or "").lower()))
    arc_json = _result_json("arc summary")
    base_json = _result_json("baseline total")
    return caps, [
        ("baseline pinned with the true total",
         "baseline total" in titles and
         str(loc_sum) in _denum(all_text(caps[:2]) + caps[1]["text"])),
        ("Baseline total result RECORDS the true total (durable metadata)",
         str(loc_sum) in base_json),
        ("Arc summary result RECORDS both totals (durable metadata)",
         str(loc_sum) in arc_json and str(rem_sum) in arc_json),
        ("foreground answered while bg ran (arc not blocked)",
         fg_done_fast and str(loc_max) in fg_txt and str(loc_arg) in fg_txt),
        ("ambiguous 'the remote one' resolved to Remote series "
         "(its max/argmax, not local's)",
         str(rem_max) in amb_txt and str(rem_arg) in amb_txt),
        ("background total delivered", str(rem_sum) in amb_txt),
        ("empty-filter ask surfaced honestly (no invented ratio)",
         honest_empty),
        ("steer recovered with the true ratio",
         str(ratio) in steer_txt or "22.77" in steer_txt
         or "22.8" in steer_txt),
        ("synthesis result pinned", "arc summary" in titles),
        ("fresh-thread re-entry names both totals",
         str(loc_sum) in re_txt and str(rem_sum) in re_txt),
    ]


@scenario("mn_cbe_kernel")
def mn_cbe_kernel(client, pid, tid):
    """Interactive SESSION on the real cluster: two sequential direct steps
    on cbe sharing in-memory state through the persistent remote kernel —
    against real partition caps (the walltime clamp's target environment)
    and the honest first-use cost (the env realizes on the cluster once).
    The state hand-off is the point: step 2 must NOT recompute."""
    if not CBE_OK:
        return [], [("cbe.next available (scenario skipped otherwise)", False)]
    expected = str(sum((i * 13) % 7 for i in range(1, 50001)))
    caps = [drive_turn(client, pid, tid,
        "On machine 'cbe', run two quick steps IN SEQUENCE, each directly "
        "(not background): (1) compute x = the sum of (i*13) mod 7 for i "
        "from 1 to 50000, keep x in memory, and print STEP1-OK; "
        "(2) WITHOUT recomputing anything — reuse x from step 1's memory — "
        "print exactly CBEKR=<x>. Then tell me the value.",
        timeout_s=1800)]
    steps = [t for t in tools_named(caps, "run_python")
             if t["input"].get("site") == "cbe"]
    session_used = any((t.get("result") or {}).get("execution_mode")
                       == "remote-session" for t in steps)
    txt = _denum(all_text(caps) + "\n" + agent_text(client, pid, tid))
    return caps, [
        ("two direct steps ran on cbe", len(steps) >= 2),
        ("persistent session actually used on the REAL cluster",
         session_used),
        ("true value handed across steps", f"CBEKR={expected}" in
         txt.replace(" ", "") or _denum(expected) in txt),
    ]


@scenario("mn_cbe_gpu")
def mn_cbe_gpu(client, pid, tid):
    """GPU ROUTING on the real cluster: a GPU-flagged step must carry the
    gpu resource so weft lands it on the g partition (1 node). Routing is
    the claim — the job may legitimately PEND if the node is busy, so
    scheduler truth accepts running/completed/pending ON PARTITION g.

    EXPECTED RED until misc/bug4_weft_gpu_partition.md lands in weft: the
    submit carries --gres but no --partition, so the default (GPU-less)
    partition swallows the job — the canary for real-cluster GPU work."""
    if not CBE_OK:
        return [], [("cbe.next available (scenario skipped otherwise)", False)]

    def _jobs_on_g():
        out = cssh("squeue -u peter.kharchenko -h -o '%i %P'; "
                   "sacct -u peter.kharchenko -S now-1hours --noheader "
                   "-X -o JobID,Partition 2>/dev/null")
        return [ln for ln in (out.stdout or "").splitlines()
                if ln.strip().endswith(" g") or ln.strip().endswith("\tg")
                or (len(ln.split()) == 2 and ln.split()[1] == "g")]
    pre = set(_jobs_on_g())
    caps = [drive_turn(client, pid, tid,
        "On machine 'cbe', run a BACKGROUND job that needs ONE GPU (flag it "
        "as a GPU workload, estimate ~2 minutes): it should just print the "
        "node's hostname. Use the node's own system python (env 'system'). "
        "Tell me once it's submitted — no need to wait for the result.",
        timeout_s=900)]
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("site") == "cbe" and t["input"].get("background")]
    gpu_asked = any(int(t["input"].get("est_gpu") or 0) >= 1 for t in bg)
    routed = False
    t0 = time.time()
    while time.time() - t0 < 120 and not routed:
        routed = len(set(_jobs_on_g()) - pre) > 0
        if not routed:
            time.sleep(10)
    wait_jobs_settled(client, pid, timeout_s=300)   # let it finish/cancel
    return caps, [
        ("background job submitted on cbe", bool(bg)),
        ("the step was flagged as a GPU workload (est_gpu)", gpu_asked),
        ("REAL scheduler truth: a new job on partition g", routed),
    ]


@scenario("mn_missing_then_recover")
def mn_missing_then_recover(client, pid, tid):
    """BAD-INPUTS on a remote (release_test_plan): the user's path has a typo
    — the file does NOT exist on the site. The agent must surface the missing
    file honestly (no fabricated numbers, no invented file), and then RECOVER
    in the same thread when the user supplies the real path. The job rows for
    the failed attempt must read honest, and the truth sweep must stay clean."""
    real = R_DATA + "-mr/series.csv"
    hssh(f"mkdir -p {R_DATA}-mr && (echo t,y; seq 1 300 | "
         f"awk '{{print $1\",\"($1*11)%29}}') > {real}")
    expected = str(sum((i * 11) % 29 for i in range(1, 301)))
    caps = [drive_turn(client, pid, tid,
        f"On machine 'hpc', read {R_DATA}-mr/serie.csv (a t,y table) and "
        f"tell me the sum of the y column.")]          # note the TYPO: serie.csv
    t1 = caps[0]["text"]
    # the real file sits NEXT to the typo'd name, so a good agent may find it
    # itself — that is fine (better than fine) as long as the path problem is
    # DISCLOSED. The failure this scenario guards against is SILENT
    # substitution: a number with no mention that the given path was wrong.
    disclosed = any(w in t1.lower() for w in
                    ("not found", "no such", "didn't exist", "doesn't exist",
                     "does not exist", "did not exist", "missing",
                     "couldn't find", "could not find", "typo", "isn't at",
                     "not at that path", "actual file"))
    silent_sub = (_denum(expected) in _denum(t1)) and not disclosed
    caps.append(drive_turn(client, pid, tid,
        f"Sorry — my typo. It's {real}."))
    t2 = _denum(caps[-1]["text"] + "\n" +
                agent_text(client, pid, tid))
    return caps, [
        ("path problem disclosed (no pretending the given path worked)",
         disclosed),
        ("no SILENT substitution (a number without the disclosure)",
         not silent_sub),
        ("true sum delivered once the path is settled",
         _denum(expected) in t2),
    ]


@scenario("mn_bundle_header_drift")
def mn_bundle_header_drift(client, pid, tid):
    """The messy-multifile persona's REAL failure mode: a two-part export
    where part B renamed a column (val → amount) and reordered the columns.
    Register as ONE dataset and total the measure across both parts: the
    agent must NOTICE the schema drift, disclose it, and produce the true
    total — not silently misparse part B (a positional read would grab ids)
    and not fabricate."""
    ddir = R_DATA + "-hd"
    hssh(f"mkdir -p {ddir} && "
         f"(echo id,val; seq 0 149 | awk '{{print $1\",\"($1*2)%9}}') "
         f"> {ddir}/part_a.csv && "
         f"(echo amount,id; seq 150 299 | awk '{{print ($1*2)%9\",\"$1}}') "
         f"> {ddir}/part_b.csv")
    expected = str(sum((i * 2) % 9 for i in range(300)))
    caps = [drive_turn(client, pid, tid,
        f"The directory {ddir} on machine 'hpc' holds a two-part quarterly "
        f"export (part_a.csv, part_b.csv) — same measurements, but the "
        f"exports may not be perfectly consistent. Register it as ONE "
        f"dataset called 'HD export' homed on hpc by reference, then "
        f"compute the total of the measurement column across BOTH parts "
        f"and tell me the total.", timeout_s=1200)]
    txt = _denum(all_text(caps) + "\n" + agent_text(client, pid, tid))
    ds = [d for d in find_entities(type="dataset", not_deleted=True)
          if "hd export" in (d.get("title") or "").lower()]
    disclosed = any(w in txt.lower() for w in
                    ("renam", "amount", "different column", "column name",
                     "header", "schema", "inconsistent", "differs",
                     "reordered", "different name"))
    return caps, [
        ("ONE dataset entity for the bundle", len(ds) == 1),
        ("true total across both drifted parts", _denum(expected) in txt),
        ("schema drift disclosed, not silently smoothed over", disclosed),
    ]


@scenario("mn_cbe_smoke")
def mn_cbe_smoke(client, pid, tid):
    """REAL-cluster smoke (cbe.next, slurm 26.05, real slurmdbd): one
    background job through the genuine scheduler — submit → sbatch → poll →
    result → honesty — with sacct as ground truth (unlike the fixture, sacct
    is populated here). The step is told to use the node's own python
    (env 'system'): first live exercise of the P2 lever, and it keeps the
    smoke from realizing a full pack through the jump host."""
    if not CBE_OK:
        return [], [("cbe.next available (scenario skipped otherwise)", False)]

    def _sacct_ids():
        out = cssh("sacct -u peter.kharchenko -S now-2hours --noheader "
                   "-o JobID,State -X 2>/dev/null")
        rows = {}
        for ln in (out.stdout or "").splitlines():
            parts = ln.split()
            if len(parts) >= 2:
                rows[parts[0]] = parts[1]
        return rows

    pre = set(_sacct_ids())
    answer = str(sum((i * i) % 97 for i in range(1, 200001)))
    caps = [drive_turn(client, pid, tid,
        "On machine 'cbe', run a BACKGROUND job: compute the sum of "
        "(i*i) mod 97 for i from 1 to 200000 and print exactly "
        "CBETOTAL=<result>. Estimate it honestly as about 2 minutes on "
        "1 core. Use the node's own system python (env 'system') — do NOT "
        "build or ship an environment for this. Tell me when it's done.",
        timeout_s=900)]
    wait_jobs_settled(client, pid, timeout_s=900)
    full = _denum(all_text(caps)) + "\n" + wait_for_text(
        client, pid, tid, answer, timeout_s=600)
    bg = [t for t in tools_named(caps, "run_python")
          if t["input"].get("background") and t["input"].get("site") == "cbe"]
    sys_env = any(str(t["input"].get("env") or "").lower()
                  in ("system", "none") for t in bg)
    post = _sacct_ids()
    new_done = [j for j, st in post.items()
                if j not in pre and st.startswith("COMPLETED")]
    return caps, [
        ("submitted a background job on cbe", bool(bg)),
        ("step used the node's system python (P2 lever, no pack realize)",
         sys_env),
        ("correct total reported (via continuation)", answer in full),
        ("REAL scheduler ground truth: a new sacct job COMPLETED",
         bool(new_done)),
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

    # optional THIRD site: cbe.next, a REAL slurm cluster (jump-host ssh).
    # Only probed when the smoke is actually selected — the probe costs ~5s
    # of jump-host latency every run otherwise.
    global CBE_OK
    if (only is None or only & {"mn_cbe_smoke", "mn_cbe_kernel",
                                "mn_cbe_gpu"}):
        if cssh("echo ok").stdout.strip() == "ok":
            try:
                r = c.sync_call("register_site", "cbe", "slurm",
                                {"root": C_ROOT, "host": "cbe.next",
                                 "durable": True})
                CBE_OK = r.get("site") == "cbe"
                print("[mn] cbe.next (REAL slurm) registered" if CBE_OK
                      else f"[mn] cbe registration returned {r}")
            except Exception as e:  # noqa: BLE001
                print("[mn] cbe registration failed — smoke skips:", e)
        else:
            print("[mn] cbe.next unreachable — smoke skips")

    from fastapi.testclient import TestClient
    from main import app
    scenarios = [(fn._scenario, fn) for fn in
                 [mn_size_up, mn_hop_chain, mn_status_surfaces, mn_honesty,
                  mn_isolated_env_remote, mn_crash_fix_rerun, mn_fanout_gather,
                  mn_pin_remote_result, mn_external_ref_inject,
                  mn_background_monitor, mn_provenance_after_chain,
                  mn_preflight_disconnect, mn_reference_drift,
                  mn_gpu_routing, mn_slurm_sized_walltime,
                  mn_plan_approval_remote,
                  mn_timeout_kill_honesty, mn_scenario_branch,
                  mn_cancel_background, mn_offline_honesty,
                  mn_rerun_asis_recomputes,
                  mn_data_gravity_recall, mn_conflicting_gravity,
                  mn_cross_thread_separation, mn_concurrent_threads_one_node,
                  mn_net_drop_midjob, mn_mid_chain_steering,
                  mn_repeat_sync, mn_interrupt_sync, mn_first_use,
                  mn_system_env_session, mn_fetch_register_verify,
                  mn_env_lifecycle_arc, mn_env_reclaim,
                  mn_cbe_smoke, mn_missing_then_recover,
                  mn_bundle_header_drift, mn_cbe_kernel, mn_cbe_gpu,
                  mn_long_arc]]
    # --only must NAME REAL scenarios: an unmatched filter ran ZERO scenarios
    # and printed ALL PASS vacuously (the exact bug class this study hunts)
    if only:
        unknown = only - {n for n, _ in scenarios}
        if unknown:
            sys.exit(f"[mn] --only names unknown scenarios: {sorted(unknown)}; "
                     f"known: {sorted(n for n, _ in scenarios)}")
    try:
        with TestClient(app) as client:
            try:
                for name, fn in scenarios:
                    if only and name not in only:
                        continue
                    run_scenario(client, name, fn)
            finally:
                for site in ("hpc", "mendel", "cbe"):
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
        if CBE_OK:
            # a realized env is multi-GB of small files on NFS — a foreground
            # rm blows the ssh timeout (seen live); detach it on the far side
            cout = cssh(f"nohup rm -rf {C_ROOT} >/dev/null 2>&1 & "
                        f"echo cleaning-detached")
            print("[cleanup] cbe dirs:", cout.stdout.strip() or cout.stderr[-120:])
    if not RESULTS:
        sys.exit("[mn] ZERO scenarios ran — refusing to report ALL PASS")
    print("\nMULTINODE:", "ALL PASS" if all(ok for _, ok in RESULTS)
          else "FAILURES: " + ", ".join(n for n, ok in RESULTS if not ok))
    sys.exit(0 if all(ok for _, ok in RESULTS) else 1)


if __name__ == "__main__":
    main()
