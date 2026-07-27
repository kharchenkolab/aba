"""Workflow-shaped LIVE regtest — real agent, real sites, running deployment.

WHY A NEW FILE. `regtest/datasets/multinode.py` is the mature substrate study:
38 scenarios over a TestClient app with a throwaway home, covering placement,
env lifecycle, GPU routing, slurm walltime sizing. It asks "does the substrate
layer behave?".

This file asks a different question, and it is the one two weeks of live
sessions kept answering badly: **does an ordinary end-to-end WORKFLOW leave the
user with something they can find, open and trust?** The failures were never in
one verb — they were in the seams between them (a run hands back a handle no
door accepts; outputs land where nothing harvests; a chdir silently untracks
everything after it).

So the scenarios here are shaped like the real sessions were, measured from the
project's tool histogram: run_r/run_python on a remote site dominate, then
Skill → plan → viewer → register. Each scenario drives REAL turns against the
RUNNING server (not a TestClient) so it exercises the deployment the user
actually uses, including its recipe pack and its registered sites.

TWO RULES, both learned the hard way:

  * **Un-prescribed prompts.** State the OUTCOME the user wants, never the
    mechanism. A prompt that says "write it in the run's working directory"
    tests obedience and cannot fail for the reason that matters — the fixture
    scenario phrased that way stayed green through a live session that produced
    ZERO tracked outputs.
  * **Assert on RECORDED state, not on what the agent claims.** The agent said
    "tracked as outputs" in a run where nothing was tracked; it was right the
    second time and wrong the first, and only the graph could tell them apart.

Run:  python regtest/live/workflows.py [--site NAME] [--only a,b] [--keep]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
RUNTIME = Path.home() / ".aba" / "runtime" / "projects"
RESULTS: list = []
# (pid, tid) -> captures from each drive(), so friction_sweep can report
# transport-level findings (a stream that never closed) alongside the rest.
_LAST_CAPS: dict = {}


# ── transport ────────────────────────────────────────────────────────────────

def api(method: str, path: str, body=None, timeout: int = 1800):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "detail": e.read().decode()[:300]}
    return json.loads(raw) if raw.strip() else {}


def drive(pid: str, tid: str, text: str, timeout: int = 2400) -> dict:
    """One REAL agent turn against the running server. Captures the same stream
    the browser sees, so a turn that dies mid-flight is visible rather than
    silently empty."""
    body = json.dumps({"text": text, "project_id": pid, "thread_id": tid}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    cap = {"prompt": text, "text": [], "tools": [], "errors": [], "run_id": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                line = line.decode(errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                t = ev.get("type")
                if t == "delta":
                    cap["text"].append(ev.get("text") or "")
                elif t == "tool_start":
                    cap["tools"].append(ev.get("name"))
                elif t == "manifest":
                    cap["run_id"] = ev.get("run_id")
                elif t in ("error", "cancelled"):
                    cap["errors"].append(str(ev.get("text") or ev.get("reason")))
                elif t == "done":
                    # Stop at `done` — the turn is over and that is what a
                    # client should act on. Reading on until the socket closes
                    # made the harness ABSORB a stream that never terminated:
                    # three concurrent turns each finished, wrote their
                    # messages, and left the connection ESTABLISHED for the full
                    # 40-minute timeout, so a real server bug looked like a slow
                    # scenario. An instrument must surface a hang, not wait it
                    # out — so record whether the stream also CLOSED, and keep
                    # going either way.
                    cap["done"] = True
                    try:
                        r.fp.raw._sock.settimeout(3)   # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001 — cannot poke the socket:
                        break          # record NOTHING rather than a false finding
                    try:
                        # b"" == the server closed. Anything else, or a timeout,
                        # means the connection is still open after `done`.
                        cap["stream_not_closed"] = bool(r.readline())
                    except Exception:  # noqa: BLE001 — read timed out: still open
                        cap["stream_not_closed"] = True
                    break
    except Exception as e:  # noqa: BLE001 — a dead turn is a FINDING, not a crash
        cap["errors"].append(f"{type(e).__name__}: {e}")
    cap["text"] = "".join(cap["text"]).strip()
    _LAST_CAPS.setdefault((pid, tid), []).append(cap)
    return cap


# ── reading RECORDED state (never the agent's claims) ───────────────────────

def tool_results(pid: str, tid: str) -> list[dict]:
    """Every tool result this thread recorded, parsed. The ground truth for
    'what did the platform actually say', as opposed to what the agent
    reported to the user."""
    db = RUNTIME / pid / "project.db"
    if not db.exists():
        return []
    out: list[dict] = []
    c = sqlite3.connect(str(db))
    try:
        rows = c.execute("select content from messages where thread_id=? order by id",
                         (tid,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    for (content,) in rows:
        try:
            blocks = json.loads(content)
        except Exception:
            continue
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            t = b.get("content")
            kinds: list = []
            if isinstance(t, list):
                # A vision result is NOT json — it is a list of content blocks
                # (text + image_ref). Joining and json-parsing threw the block
                # TYPES away, so "did the agent actually see the image?" could
                # not be answered and read as a failure on a turn that worked.
                kinds = [x.get("type") for x in t if isinstance(x, dict)]
                t = " ".join(str(x.get("text", "")) for x in t if isinstance(x, dict))
            try:
                rec = json.loads(t)
            except Exception:
                rec = {"_raw": str(t)[:400]}
            if kinds:
                rec["_block_types"] = kinds
            out.append(rec)
    return out


def tracked_outputs(pid: str) -> list[tuple]:
    """(name, state, kind) for everything on this project's Run cards.

    Reads the durable view as the TREE it is. Reading it as {"files": [...]}
    reported a fully-tracked run as untracked — a false negative in the
    instrument, which is exactly the failure mode this suite exists to catch."""
    ents = api("GET", f"/api/entities?project_id={pid}")
    ents = ents if isinstance(ents, list) else ents.get("entities", [])
    found: list[tuple] = []

    def walk(node):
        for ch in (node.get("children") or []):
            if ch.get("kind") == "file":
                found.append((ch.get("name"), ch.get("state"), ch.get("art_kind")))
            walk(ch)

    for r in [e for e in ents if e.get("type") == "analysis"]:
        walk(api("GET", f"/api/runs/{r['id']}/durable?project_id={pid}"))
    return found


def entities(pid: str, *types) -> list[dict]:
    ents = api("GET", f"/api/entities?project_id={pid}")
    ents = ents if isinstance(ents, list) else ents.get("entities", [])
    return [e for e in ents if not types or e.get("type") in types]


def notes_containing(pid: str, tid: str, needle: str) -> list[str]:
    return [r["note"] for r in tool_results(pid, tid)
            if isinstance(r.get("note"), str) and needle in r["note"]]


def hrefs_offered(text: str) -> list[str]:
    """Every in-app link the agent actually handed the user. `/artifacts/...`
    (a served file) and `/viewer/...` (an external launch) are BOTH honest
    answers to "give me a link" — which one is right depends on the format, so
    a check may only require that SOME link was offered, never a specific
    mechanism."""
    return re.findall(r"\((/(?:artifacts|viewer)[^\s)]+)\)", text or "")


def link_resolves(href: str) -> bool:
    """The outcome behind a link: does it actually serve? A ranged/oversize
    honest answer counts; a 404 does not."""
    req = urllib.request.Request(BASE + href, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status in (200, 206) and bool(r.read(1))
    except urllib.error.HTTPError as e:
        return e.code == 413          # honest "too big to inline" still resolves
    except Exception:                 # noqa: BLE001
        return False


def errors_containing(pid: str, tid: str, needle: str) -> list[str]:
    out = []
    for r in tool_results(pid, tid):
        blob = json.dumps(r)[:2000]
        if needle in blob:
            out.append(blob[:200])
    return out


# ── friction sweep (runs after EVERY scenario) ──────────────────────────────
#
# A scenario's checks answer "did the workflow reach the goal". They say nothing
# about what it COST — the retried tool call, the swallowed error, the kernel
# that died and took state with it. Every bug this fortnight showed up as
# friction long before it showed up as a failure, so the sweep records it
# whether or not the checks passed.

_FRICTION_SIGNATURES = [
    ("not found",            "a door refused a handle"),
    ("no such file",         "path resolution missed"),
    ("not an exported object", "guessed an API name"),
    ("cannot open the connection", "kernel protocol write failed"),
    ("Traceback",            "uncaught exception surfaced to the agent"),
    ("platform_mismatch",    "env not locked for the site's platform"),
    ("solve_conflict",       "env unsatisfiable as pinned"),
    ("unreachable",          "site transport failed"),
    ("nonzero_exit",         "job failed without a reason the agent could use"),
    ("no result.json",       "job outcome unreadable"),
    ("substrate_offline",    "substrate not configured"),
    ("no longer its sandbox", "writes left the harvested sandbox"),
]


def friction_sweep(pid: str, tid: str) -> list[dict]:
    """Frictions the agent absorbed, whether or not the scenario passed.

    TWO sources, deliberately: the EXISTING consumption-surface oracle
    (`regtest/harness/surfaces.surface_parity_failures` — "can a person
    actually open what was computed?", the standing post-condition the sweep
    already applies to synthetic scenarios) plus signatures that are only
    visible in the CONVERSATION, which the oracle never sees because it walks
    HTTP surfaces rather than tool results: a guessed API name, a blind retry,
    a kernel that restarted and took state with it."""
    out: list[dict] = []

    # 1. the real oracle, reused rather than re-implemented
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from harness.surfaces import surface_parity_failures

        class _C:
            """The oracle's client contract: .get(url) → (.status_code, .content,
            .text, .json()). RAW BYTES, because most of what it fetches is not
            JSON — a CSV, a PNG, a range response.

            An earlier shim routed through api(), which json.loads() every body.
            Every honest 200 serving a CSV then raised JSONDecodeError, the oracle
            recorded it as `dead_link -> 599`, and a run whose outputs served
            perfectly reported four surface failures. The shim was LESS capable
            than a browser, so it manufactured the failures it was built to
            detect."""
            def get(self, url):
                import types
                # X-Project-Id, NOT `POST /api/projects/{pid}/open`: opening
                # repoints the PROCESS-GLOBAL project, which is exactly the
                # state whose drift misfiles concurrent turns' writes. An
                # auditor must not perturb what it audits. Without any project
                # context the durable-view route answers 404 and every run reads
                # as a broken surface (it did, on both sites).
                req = urllib.request.Request(
                    BASE + url, method="GET", headers={"X-Project-Id": pid})
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        body, code = r.read(), r.status
                except urllib.error.HTTPError as e:
                    body, code = e.read(), e.code
                except Exception as e:  # noqa: BLE001 — transport failure IS a surface failure
                    body, code = f"{type(e).__name__}: {e}".encode(), 599

                def _json():
                    return json.loads(body.decode("utf-8", "replace"))
                return types.SimpleNamespace(
                    status_code=code, content=body,
                    text=body.decode("utf-8", "replace"), json=_json)
        for f in surface_parity_failures(_C(), pid) or []:
            out.append({"kind": "consumption-surface parity", "signature": "surfaces",
                        "excerpt": str(f)[:200]})
    except Exception as e:  # noqa: BLE001 — the oracle is a bonus, never the gate
        out.append({"kind": "surface oracle unavailable", "signature": "surfaces",
                    "excerpt": f"{type(e).__name__}: {e}"})

    # 2. conversation-only signatures
    res = tool_results(pid, tid)
    for r in res:
        blob = json.dumps(r)[:4000]
        for sig, meaning in _FRICTION_SIGNATURES:
            if sig.lower() in blob.lower():
                out.append({"kind": meaning, "signature": sig,
                            "excerpt": _excerpt(blob, sig)})
                break
    for cap in (_LAST_CAPS.get((pid, tid)) or []):
        if cap.get("stream_not_closed"):
            out.append({"kind": "SSE stream did not close after done",
                        "signature": "stream", "excerpt":
                        "the turn finished but the connection stayed open — a "
                        "browser tab would never leave the streaming state"})
    fresh = sum(1 for r in res if "Fresh kernel" in str(r.get("stdout", "")))
    if fresh > 1:
        out.append({"kind": "kernel restarted mid-scenario (state lost)",
                    "signature": "Fresh kernel", "excerpt": f"{fresh} fresh-kernel banners"})
    # A blind retry is the SAME call made again after the previous one FAILED.
    # Both halves matter, and both were once wrong here:
    #   * the key truncated the serialized input to 200 chars, so a job
    #     resubmitted with env='system' after a platform_mismatch — the adaptive
    #     recovery this suite exists to reward — keyed identically to the
    #     original and was reported as a blind retry. Compare the WHOLE call.
    #   * a repeat of a call that SUCCEEDED is not a retry at all (a probe run
    #     twice, a deliberate re-check), so pair each call with its outcome.
    seen: dict = {}
    for name, inp, failed in _tool_calls(pid, tid):
        key = (name, inp)
        prev = seen.get(key) or {"n": 0, "after_failure": False}
        seen[key] = {"n": prev["n"] + 1,
                     # the REPEAT counts only if what it repeats had failed
                     "after_failure": prev["after_failure"] or
                     (prev["n"] > 0 and prev.get("last_failed", False)),
                     "last_failed": failed}
    for (name, inp), st in seen.items():
        if st["n"] > 1 and st["after_failure"]:
            out.append({"kind": "identical call repeated (blind retry)",
                        "signature": name, "excerpt": f"{st['n']}x {name} {inp[:90]}"})
    return out


def _excerpt(blob: str, sig: str) -> str:
    i = blob.lower().find(sig.lower())
    return blob[max(0, i - 60):i + 140].replace("\n", " ")


def _tool_calls(pid: str, tid: str) -> list:
    """(tool_name, canonical input, did_it_fail) in call order.

    The input is key-SORTED json so two calls that differ only in dict ordering
    compare equal, and full-length so two calls that differ anywhere compare
    unequal. `did_it_fail` is read from the matching tool_result (by
    tool_use_id), which arrives in a LATER message than the call."""
    db = RUNTIME / pid / "project.db"
    if not db.exists():
        return []
    c = sqlite3.connect(str(db))
    try:
        rows = c.execute("select content from messages where thread_id=? order by id",
                         (tid,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    calls: list = []
    failed_ids: set = set()
    for (content,) in rows:
        try:
            blocks = json.loads(content)
        except Exception:
            continue
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                calls.append([b.get("name"),
                              json.dumps(b.get("input", {}), sort_keys=True),
                              b.get("id")])
            elif b.get("type") == "tool_result":
                cc = b.get("content")
                blob = cc if isinstance(cc, str) else json.dumps(cc)
                bad = (b.get("is_error") or '"error"' in blob
                       or '"returncode": 1' in blob or '"ok": false' in blob)
                if bad and b.get("tool_use_id"):
                    failed_ids.add(b["tool_use_id"])
    return [(n, i, cid in failed_ids) for n, i, cid in calls]


# ── scenarios ───────────────────────────────────────────────────────────────

SCENARIOS: list = []


def scenario(name):
    def deco(fn):
        fn._scenario = name
        SCENARIOS.append((name, fn))
        return fn
    return deco


@scenario("wf_produce_view_track")
def wf_produce_view_track(pid, tid, site):
    """The core arc: produce results on a remote node, then LOOK at them.

    This is the seam every recent bug lived in. The prompt asks the agent to
    verify its own figure — which forces it to take a handle the run just gave
    it back and feed it to a viewing door. A run whose handles no door accepts
    fails here, as does a run whose outputs never became tracked."""
    cap = drive(pid, tid,
        f"On machine '{site}', make a scatter plot of y=x^2 for x in 1..50 and "
        f"save it as a PNG, plus a CSV of the same numbers. Then LOOK at the "
        f"PNG you just made and tell me in one line whether the curve really "
        f"is a parabola. I want both files to end up as outputs of this work.")
    tracked = tracked_outputs(pid)
    names = [n for n, _s, _k in tracked]
    # The vision channel shows up as an `image_ref` content block on the
    # tool result — that is the proof the agent actually SAW the pixels.
    viewed = [r for r in tool_results(pid, tid)
              if "image_ref" in (r.get("_block_types") or [])
              or r.get("kind") == "image"]
    notfound = errors_containing(pid, tid, "artifact not found") + \
        errors_containing(pid, tid, "file not found")
    return cap, [
        ("turn completed without a stream error", not cap["errors"]),
        ("a PNG and a CSV are TRACKED as run outputs",
         any(n.endswith(".png") for n in names) and
         any(n.endswith(".csv") for n in names)),
        ("the agent could VIEW its own artifact", bool(viewed)),
        ("no door refused a handle the run had just returned", not notfound),
    ]


@scenario("wf_cwd_drift_warned")
def wf_cwd_drift_warned(pid, tid, site):
    """A chdir now SURVIVES (the substrate anchors its protocol), so everything
    written relatively after it silently leaves the harvested sandbox. The
    platform must say so at the end of the step, while the agent can still act
    — and the kernel must not die, which it used to."""
    cap = drive(pid, tid,
        f"Using Python on '{site}': make a directory ~/wf_drift_out, chdir into "
        f"it, and write a small file notes.txt there. Report the working "
        f"directory before and after.")
    warned = notes_containing(pid, tid, "no longer its sandbox")
    deaths = _kernel_deaths(pid, tid)
    return cap, [
        ("turn completed", not cap["errors"]),
        ("kernel SURVIVED the chdir (used to be fatal)", not deaths),
        ("platform WARNED that writes are now untracked", bool(warned)),
        ("the warning names the register_dataset lever",
         any("register_dataset" in w for w in warned)),
    ]


@scenario("wf_env_conflict_isolates")
def wf_env_conflict_isolates(pid, tid, site):
    """A capability whose pins contradict the base pack must NOT be installed
    into the project's default session: that leaves the session unfreezable and
    silently breaks every later remote step. It must route to an isolated env,
    and the default env must still freeze afterwards."""
    cap = drive(pid, tid,
        f"I need the 'panhumanpy' package available to run some Python on "
        f"'{site}'. Set that up and then just print its version — nothing else.")
    health = _snapshot_health(pid)
    isolated = [e for e in entities(pid) if "env" in str(e.get("type", ""))]
    return cap, [
        ("turn completed", not cap["errors"]),
        ("the project's default env is STILL freezable (not poisoned)",
         health.get("ok") is not False),
        ("the conflicting package did not enter the default session",
         "panhumanpy" not in json.dumps(health.get("additions") or [])),
    ]


@scenario("wf_slurm_batch")
def wf_slurm_batch(pid, tid, site):
    """A batch job on a REAL scheduler: queued, run, settled, and its outputs
    tracked like any other run's. The queue lane is where 'it finished but
    nothing came back' hides."""
    cap = drive(pid, tid,
        f"Submit a BACKGROUND batch job to '{site}' that sleeps ~20 seconds and "
        f"then writes a CSV called batch_result.csv with a few rows. Tell me when "
        f"it's queued; I'll check back.")
    _wait_jobs_settled(pid, timeout_s=600)
    jobs = api("GET", "/api/jobs")
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    mine = [j for j in jobs if (j.get("params") or {}).get("project_id") == pid]
    tracked = [n for n, _s, _k in tracked_outputs(pid)]
    return cap, [
        ("turn completed", not cap["errors"]),
        ("a background job was created", bool(mine)),
        ("the job reached a terminal state (not stuck queued)",
         all(str(j.get("status")) not in ("queued", "running") for j in mine) if mine else False),
        ("the batch output is TRACKED", any("batch_result" in n for n in tracked)),
    ]


@scenario("wf_state_persists_then_recovers")
def wf_state_persists_then_recovers(pid, tid, site):
    """Interactive multi-step is the dominant shape (run_r/run_python were 119
    of ~190 calls in the real project). Two things must hold: state persists
    across turns, and when the kernel is gone the agent is TOLD rather than
    left guessing at a missing variable."""
    c1 = drive(pid, tid,
        f"On '{site}', in Python, create a variable called wf_secret holding the "
        f"number 4242. Just confirm it exists.")
    c2 = drive(pid, tid,
        f"Still on '{site}': print wf_secret, and tell me its value.")
    fresh = [r for r in tool_results(pid, tid)
             if "Fresh kernel" in str(r.get("stdout", ""))]
    return [c1, c2], [
        ("both turns completed", not c1["errors"] and not c2["errors"]),
        ("state PERSISTED across turns (4242 recalled)",
         "4242" in c2["text"] or "4242" in json.dumps(tool_results(pid, tid))[-4000:]),
        ("a fresh kernel, if any, announced itself rather than failing silently",
         True if not fresh else any("in-memory state" in str(r.get("stdout", "")).lower()
                                    for r in fresh)),
    ]


@scenario("wf_platform_mismatch_recovery")
def wf_platform_mismatch_recovery(pid, tid, site):
    """CAN THE AGENT ADAPT? — an arm64 site the project's DEFAULT env cannot run.

    The default env is locked for the platforms it was solved on; an aarch64
    Linux node is not one of them, and by design the default env does NOT
    re-lock (only NAMED envs do). So a background job there fails with
    env.platform_mismatch. That is not a defect to engineer around — it is a
    recoverable obstacle, and the whole question is whether the platform tells
    the agent enough to recover.

    It did not: the failure surfaced as "infra failure before the entry ran?",
    and the agent blamed the site and re-submitted the identical job — twice.
    With the typed verdict and an ABA-shaped lever (make_isolated_env, which
    re-locks for the site automatically), recovery should be reachable.

    The prompt states the OUTCOME and grants permission to sort out obstacles;
    it never names the obstacle, the env, or the verb."""
    cap = drive(pid, tid,
        f"On machine '{site}', run this as a BACKGROUND job: compute the first "
        f"200 primes and write them to primes.csv. If something about the "
        f"environment gets in the way, sort it out yourself — I just want the "
        f"finished CSV as an output of this work.")
    _wait_jobs_settled(pid, timeout_s=900)
    jobs = api("GET", "/api/jobs")
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    mine = [j for j in jobs if (j.get("params") or {}).get("project_id") == pid]
    done = [j for j in mine if str(j.get("status")) in ("done", "succeeded", "finished")]
    tracked = [n for n, _s, _k in tracked_outputs(pid)]
    saw_mismatch = any("platform_mismatch" in str(j.get("error") or "") for j in mine)
    adapted = any(t in ("make_isolated_env",) for t in cap["tools"])
    return cap, [
        ("turn completed", not cap["errors"]),
        ("the platform obstacle was actually encountered (ARMED)", saw_mismatch
         or bool(done)),
        ("the agent ADAPTED rather than repeating the same failing submit",
         adapted or bool(done)),
        ("a job ultimately SUCCEEDED", bool(done)),
        ("primes.csv is TRACKED", any("primes" in n for n in tracked)),
    ]


@scenario("wf_cross_language_handoff")
def wf_cross_language_handoff(pid, tid, site):
    """R produces, Python consumes — the seam that cost a live session three
    round trips and a silently-wrong file.

    What went wrong before: the agent reached for `lstar::write_anndata` (a
    PYTHON-only name), got 'not an exported object', then forced
    `lstar_write(d, "x.h5ad")` which silently wrote a zarr STORE under an .h5ad
    name — discovered only later when the Python side found a directory. The
    pack now names the asymmetry and the supported bridge at the point of need.

    Un-prescribed: the user asks for the OUTCOME (numbers computed in R, read in
    Python), never the format or the function."""
    cap = drive(pid, tid,
        f"On '{site}': compute a small table in R (say 20 rows of x and x^2), "
        f"then hand that data over to PYTHON on the same machine and print its "
        f"shape and column names from Python. Use whatever interchange you "
        f"think is right — I just want the Python side to read what R made.")
    res = tool_results(pid, tid)
    langs = {r.get("execution_mode"): 1 for r in res}
    used_r = any("run_r" in t for t in cap["tools"])
    used_py = any("run_python" in t for t in cap["tools"])
    # the two failure signatures from the live incident
    bad_name = errors_containing(pid, tid, "not an exported object")
    misnamed = [r for r in res
                if "is.dir" in json.dumps(r) and "h5ad" in json.dumps(r)]
    return cap, [
        ("turn completed", not cap["errors"]),
        ("both languages were actually used", used_r and used_py),
        ("no guessed-API failure ('not an exported object')", not bad_name),
        ("Python side reported the data it read",
         any(k in cap["text"].lower() for k in ("20", "shape", "column"))),
    ]


@scenario("wf_viewer_link_remote")
def wf_viewer_link_remote(pid, tid, site):
    """A remote result must become something the USER can open. The last mile of
    every analysis, and it failed live for absolute remote paths until
    get_viewer_url learned to say `register_dataset(path=, site=)` and
    view_artifact grew a remote tier.

    The check asserts the OUTCOME (a link was offered and it serves), never the
    MECHANISM. An earlier version required a `viewer_url` / `/viewer` href and
    so failed a session in which the platform was entirely correct: a CSV has no
    external viewer, `get_viewer_url` said exactly that, and the agent handed
    over a working `/artifacts/...` link. Prescribing the mechanism made the
    right answer unrepresentable."""
    cap = drive(pid, tid,
        f"On '{site}', save a small CSV of 30 random numbers, then give me a "
        f"link I can click to look at it.")
    links = hrefs_offered(cap["text"])
    viewer = [r for r in tool_results(pid, tid)
              if isinstance(r, dict) and r.get("viewer_url")]
    dead = [h for h in links if not link_resolves(h)]
    refusals = errors_containing(pid, tid, "No file matching")
    return cap, [
        ("turn completed", not cap["errors"]),
        # ARMED: no link at all is a failure, so "all links resolve" can never
        # pass vacuously on a turn that offered none.
        ("the user was handed a clickable link", bool(links) or bool(viewer)),
        (f"every offered link RESOLVES ({len(links)} checked)",
         bool(links or viewer) and not dead),
        ("no unrecoverable path refusal", not refusals or bool(links or viewer)),
    ]


# ── helpers over recorded state ─────────────────────────────────────────────

def _kernel_deaths(pid: str, tid: str) -> list:
    kernels = {(r.get("compute") or {}).get("kernel_id")
               for r in tool_results(pid, tid)}
    kernels.discard(None)
    if not kernels:
        return []
    wdb = Path.home() / ".aba" / "weft" / ".weft" / "state.db"
    if not wdb.exists():
        return []
    c = sqlite3.connect(str(wdb))
    dead = []
    for k in kernels:
        row = c.execute(
            "select count(*) from events where kind='kernel.died' "
            "and json_extract(payload,'$.kernel')=?", (k,)).fetchone()
        if row and row[0]:
            dead.append(k)
    return dead


def _snapshot_health(pid: str) -> dict:
    """Is the project's default python env still freezable? Read through the
    same code path inspect_env uses."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    try:
        from core.compute import adapter, project_env
        adapter.configure()
        return project_env.snapshot_health(pid, "python")
    except Exception as e:  # noqa: BLE001
        return {"ok": None, "error": str(e)}


def _wait_jobs_settled(pid: str, timeout_s: int = 600) -> None:
    end = time.time() + timeout_s
    while time.time() < end:
        jobs = api("GET", "/api/jobs")
        jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
        mine = [j for j in jobs if (j.get("params") or {}).get("project_id") == pid]
        if mine and all(str(j.get("status")) not in ("queued", "running")
                        for j in mine):
            return
        time.sleep(10)


# ── runner ──────────────────────────────────────────────────────────────────

# ── concurrency: many threads at once against ONE compute node ──────────────
#
# The shape every other scenario here misses. Each of those drives ONE thread to
# completion, so nothing crosses: a defect that only appears when two turns are
# in flight is invisible to all of them. Two projects driven concurrently is what
# actually surfaced the cross-project write leak (2026-07-27) — records, harvest
# dirs and artifacts filed under a bystander project — and that only happened
# because two SWEEPS were run at once by hand. This makes it a first-class
# scenario so it is exercised on purpose rather than by luck.
#
# Two axes, because they fail differently:
#   * CROSS-PROJECT — separate projects, separate DBs. Failure = misfiled rows
#     (audited by regtest/harness/project_isolation.py).
#   * SAME-PROJECT, MANY THREADS — one DB, one site, N kernels. Failure = one
#     thread's state or outputs appearing in another's, or kernel-pool crosstalk.

def _thread_body(pid, tid, site, tag, value):
    """One thread's work: bind a distinctive value in a persistent kernel, write
    a file named after itself, then read BOTH back. Distinctive per thread, so
    any crosstalk is unambiguous rather than plausible."""
    cap = drive(pid, tid,
        f"On '{site}': set a Python variable tag = '{tag}' and n = {value}, "
        f"then write a one-line CSV called {tag}.csv containing that tag and "
        f"number. Report what you wrote.")
    cap2 = drive(pid, tid,
        f"On '{site}': print the value of tag and n from memory, and print the "
        f"contents of {tag}.csv. Report both exactly.")
    return cap, cap2


def _concurrent(jobs):
    """Run thunks concurrently and return their results in order. Threads, not
    tasks: each drive() is a blocking HTTP call to the live server, which is what
    a browser with several tabs open does."""
    import threading
    out: dict = {}

    def _run(i, f):
        try:
            out[i] = f()
        except Exception as e:  # noqa: BLE001 — a dead lane is a FINDING
            out[i] = e

    ts = [threading.Thread(target=_run, args=(i, f)) for i, f in enumerate(jobs)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return [out.get(i) for i in range(len(jobs))]


def wf_concurrent_threads_one_site(pid, tid, site, n=3):
    """N threads in ONE project, all hitting the same compute node at once.

    Not registered via @scenario: it needs its own thread fan-out, so run_one's
    single-thread contract does not fit. Driven by run_concurrent below.
    """
    tags = [f"lane{i}" for i in range(n)]
    tids = [tid] + [api("POST", "/api/threads",
                        {"project_id": pid, "title": f"concurrent-{t}"}).get("id")
                    for t in tags[1:]]
    results = _concurrent([
        (lambda p=pid, t=tids[i], g=tags[i], v=(1000 + i):
         _thread_body(p, t, site, g, v))
        for i in range(n)])

    checks = []
    live = [r for r in results if not isinstance(r, Exception)]
    checks.append((f"all {n} concurrent threads completed", len(live) == n))

    for i, r in enumerate(results):
        tag, val = tags[i], 1000 + i
        if isinstance(r, Exception):
            checks.append((f"{tag}: turn survived", False))
            continue
        cap1, cap2 = r
        txt = (cap2.get("text") or "")
        # The load-bearing assertions: each thread must see ITS OWN state, and
        # must NOT see any other thread's. A check that only looked for its own
        # value would pass while the text also carried a sibling's.
        others = [t for j, t in enumerate(tags) if j != i]
        checks.append((f"{tag}: recalled its own state ({val})", str(val) in txt))
        checks.append((f"{tag}: no other lane's tag leaked in",
                       not any(o in txt for o in others)))

    # Every thread's file must be attributed to ITS OWN thread's records.
    per_thread = {}
    for i, t in enumerate(tids):
        per_thread[tags[i]] = [n for n, _i, _f in _tool_calls(pid, t)]
    checks.append(("each thread recorded its own tool calls",
                   all(v for v in per_thread.values())))
    return {"text": "", "errors": []}, checks


def run_concurrent(site, n=3, keep=False):
    name = f"wf_concurrent_threads_x{n}"
    proj = api("POST", "/api/projects", {"title": f"wf-{name}-{int(time.time())}"})
    pid = proj.get("id")
    tid = api("POST", "/api/threads", {"project_id": pid, "title": name}).get("id")
    print(f"\n=== {name}  (site={site} project={pid})", flush=True)
    t0 = time.time()
    try:
        caps, checks = wf_concurrent_threads_one_site(pid, tid, site, n=n)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        checks = [(f"scenario raised: {type(e).__name__}: {e}", False)]
    dt = time.time() - t0
    for label, ok in checks:
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}")
    frictions = friction_sweep(pid, tid)
    for f in frictions:
        print(f"   [friction] {f['kind']}: {f['excerpt'][:120]}")
    RESULTS.append({"name": name, "pid": pid, "secs": round(dt), "checks": checks,
                    "frictions": frictions,
                    "failed": [c for c, ok in checks if not ok]})
    print(f"   ({dt:.0f}s)")
    return pid


def run_cross_project(site, n=3, keep=False):
    """N PROJECTS driven at once against one node, then audited for misfiled
    records. This is the exact condition that produced the 2026-07-27 leak: a
    project created while another project's remote exec was in flight repointed
    the process-global, and the in-flight turn's writes followed it.

    Deliberately creates each project AFTER the previous turns are already
    running — creation is the event that moves the global, so a version that
    created all projects up front would never reproduce it.
    """
    name = f"wf_cross_project_x{n}"
    print(f"\n=== {name}  (site={site})", flush=True)
    t0 = time.time()
    import threading
    pids: list = []
    started: list = []
    lock = threading.Lock()

    def lane(i):
        # stagger: project i is created while lane i-1's turn is mid-flight
        time.sleep(i * 12)
        proj = api("POST", "/api/projects",
                   {"title": f"wf-xproj-{i}-{int(time.time())}"})
        p = proj.get("id")
        t = api("POST", "/api/threads",
                {"project_id": p, "title": f"xproj-{i}"}).get("id")
        with lock:
            pids.append(p)
            started.append((p, t))
        # long enough to still be running when the NEXT project is created
        return _thread_body(p, t, site, f"proj{i}", 2000 + i)

    res = _concurrent([(lambda i=i: lane(i)) for i in range(n)])
    dt = time.time() - t0
    checks = [(f"all {n} projects' turns completed",
               all(not isinstance(r, Exception) for r in res))]

    # Door 1: each project's records belong to its own thread. The audit is the
    # cross-project oracle — a per-project check cannot see this class at all.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
    from project_isolation import audit, _threads_of, _exec_rows  # noqa: E402
    scope = {}
    for p in pids:
        db = RUNTIME / p / "project.db"
        scope[p] = {"threads": _threads_of(db), "execs": _exec_rows(db)}
    n_exec = sum(len(v["execs"]) for v in scope.values())
    # ARMED: an audit over projects that recorded NO executions proves nothing.
    checks.append((f"the projects actually recorded executions ({n_exec})", n_exec > 0))
    bad = audit(scope)
    checks.append((f"no misfiled execution records across {n} projects",
                   not bad))
    for b in bad[:6]:
        print(f"   [leak] {b}")

    # Door 2: each project's OUTPUT is present in that project, nowhere else.
    for i, p in enumerate(pids):
        want = f"proj{i}.csv"
        mine = _project_has_output(p, want)
        checks.append((f"{want} is an output of its own project", mine))

    for label, ok in checks:
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}")
    fr = []
    for p, t in started:
        fr += friction_sweep(p, t)
    for f in fr:
        print(f"   [friction] {f['kind']}: {f['excerpt'][:120]}")
    RESULTS.append({"name": name, "pid": ",".join(pids), "secs": round(dt),
                    "checks": checks, "frictions": fr,
                    "failed": [c for c, ok in checks if not ok]})
    print(f"   ({dt:.0f}s)")
    return pids


def _project_has_output(pid: str, filename: str) -> bool:
    """Is `filename` recorded as an output entity of THIS project? Reads the
    graph, never the agent's account of it."""
    db = RUNTIME / pid / "project.db"
    if not db.exists():
        return False
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = c.execute("select title, artifact_path from entities").fetchall()
    except Exception:  # noqa: BLE001
        return False
    return any(filename in str(r[0] or "") or filename in str(r[1] or "")
               for r in rows)


def run_one(name, fn, site, keep=False):
    proj = api("POST", "/api/projects", {"title": f"wf-{name}-{int(time.time())}"})
    pid = proj.get("id")
    tid = api("POST", "/api/threads", {"project_id": pid, "title": name}).get("id")
    print(f"\n=== {name}  (site={site} project={pid})", flush=True)
    t0 = time.time()
    try:
        caps, checks = fn(pid, tid, site)
    except Exception as e:  # noqa: BLE001 — a scenario that explodes is a FAIL
        import traceback
        traceback.print_exc()
        checks, caps = [(f"scenario raised: {type(e).__name__}: {e}", False)], []
    dt = time.time() - t0
    for label, ok in checks:
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}")
    frictions = friction_sweep(pid, tid)
    for f in frictions:
        print(f"   [friction] {f['kind']}: {f['excerpt'][:120]}")
    RESULTS.append({"name": name, "pid": pid, "secs": round(dt),
                    "checks": checks, "frictions": frictions,
                    "failed": [c for c, ok in checks if not ok]})
    print(f"   ({dt:.0f}s)")
    return pid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="orbtest")
    ap.add_argument("--only", default="")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--concurrent", type=int, default=0, metavar="N",
                    help="N threads in ONE project at once against --site")
    ap.add_argument("--cross-project", type=int, default=0, metavar="N",
                    help="N projects at once against --site, then audit for "
                         "misfiled records")
    a = ap.parse_args()
    only = {s for s in a.only.split(",") if s}
    known = {n for n, _ in SCENARIOS}
    if only - known:
        sys.exit(f"--only names unknown scenarios: {sorted(only - known)}; "
                 f"known: {sorted(known)}")

    health = api("GET", "/api/health")
    if not health.get("ok"):
        sys.exit(f"live server not healthy: {health}")
    sites = api("GET", "/api/compute/sites")
    names = [s.get("name") for s in (sites.get("sites") or [])]
    print(f"live server OK; sites={names}; target={a.site}")
    if a.site not in names:
        sys.exit(f"site {a.site!r} is not registered")

    if a.concurrent or a.cross_project:
        # Concurrency lanes are opt-in and run ALONE: mixing them with the
        # sequential scenarios would make any misfiled record ambiguous about
        # which lane produced it, and diagnosing that was most of the cost the
        # first time round.
        if a.concurrent:
            run_concurrent(a.site, n=a.concurrent, keep=a.keep)
        if a.cross_project:
            run_cross_project(a.site, n=a.cross_project, keep=a.keep)
    else:
        for name, fn in SCENARIOS:
            if only and name not in only:
                continue
            run_one(name, fn, a.site, keep=a.keep)

    print("\n================= SUMMARY =================")
    bad = 0
    for r in RESULTS:
        n_fail = len(r["failed"])
        bad += bool(n_fail)
        print(f"  {'FAIL' if n_fail else 'ok  '}  {r['name']:32} {r['secs']:>4}s"
              + (f"  → {r['failed']}" if n_fail else ""))
    print(f"\n{len(RESULTS) - bad}/{len(RESULTS)} scenarios fully green")
    allf = [(r["name"], f) for r in RESULTS for f in r.get("frictions", [])]
    if allf:
        print(f"\n---------------- FRICTIONS ({len(allf)}) ----------------")
        for nm, f in allf:
            print(f"  {nm:32} {f['kind']}")
            print(f"      {f['excerpt'][:150]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
