"""
v2 scenario runner — drives a realistic multi-turn session against the LIVE agent
path, SCORES it (mechanical + an LLM-judge vision rubric), and preserves a durable
forensic bundle for post-hoc deep-dives.

Every `actor: agent` step is a real /api/chat turn (guide -> core/llm.py -> Anthropic),
so the genuine history->API transform + prompt caching run. Every `actor: user` step is
the real curation route (pin/unpin/delete/restore/make_revision). Per step the runner
captures the SSE transcript, the raw API request(s) dumped by core/llm.py (n_msgs, cache
breakpoints), the `usage` cache tokens, and the manifest/entities state — then checks
them and (for agent steps) grades science with an Opus vision judge.

Two modes (see ABA_SCENARIO_MODEL):
  - unset  -> Haiku: HARNESS-STRESS test (Haiku errs a lot; shakes out runner robustness)
  - <opus> -> SCIENCE test (are the recipes/tools good enough to solve the problem)

Forensic bundle (kept, NOT checked in): regtest/scenarios/_runs/<scenario>-<ts>/ holds the
replayable raw requests, turn-context sidecars, the per-step transcript+verdict+judge,
the project DB + artifacts, and bundle.json (step -> files navigation) for a later
Opus-1M forensic agent to root-cause failures.

    ABA_SCENARIO=_selftest_session \
      /home/pkharchenko/aba/aba_runtime/.venv/bin/python -u regtest/harness/runner.py
"""
from __future__ import annotations
import base64
import glob
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "regtest" / "scenarios"
SCENARIO = os.environ.get("ABA_SCENARIO", "_selftest_session")
_TS = time.strftime("%Y%m%d-%H%M%S", time.localtime())
RUN = LIB / "_runs" / f"{SCENARIO}-{_TS}"
RUN.mkdir(parents=True, exist_ok=True)
JUDGE_MODEL = os.environ.get("ABA_JUDGE_MODEL", "claude-opus-4-8")
DO_JUDGE = os.environ.get("ABA_NO_JUDGE", "").lower() not in ("1", "true", "yes")
CRED_KEYS = ("ABA_LLM_CREDENTIAL", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
             "ABA_HOME", "ABA_MODEL", "ABA_SYSTEM_BUNDLE", "ABA_INSTITUTION_BUNDLE",
             "ABA_SITE_CONFIG", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
RUBRIC_DIMS = ("correctness", "completeness", "no_fabrication", "lifecycle", "efficiency")
# H5: per-turn wall-clock ceiling. A wedged exec (kernel hang / zmq EAGAIN under
# load) otherwise blocks consume()'s iter_lines() forever — the whole sweep hangs
# and NO bundle is written (blast_seq s2). With a ceiling, the hung turn becomes a
# TurnTimeout the per-step handler records (verdict FAIL) + recovers from, so the
# hang is OBSERVABLE in the bundle instead of a silent void. Generous by default
# so legitimate heavy work (installs, AF-DB fetch, training) isn't false-killed.
TURN_TIMEOUT_S = float(os.environ.get("ABA_TURN_TIMEOUT_S", "600"))


class TurnTimeout(RuntimeError):
    """A single agent turn exceeded TURN_TIMEOUT_S (likely a wedged kernel/exec)."""


def call_with_timeout(fn, timeout_s: float, *args, **kwargs):
    """Run fn(*args) in a daemon thread; raise TurnTimeout if it outlives
    timeout_s. The worker is abandoned (daemon) — the caller is expected to
    restart_client() so a fresh app/session is used next. We don't try to kill the
    in-process server turn; the kernel reaper culls its idle kernel later."""
    box: dict = {}
    def _run():
        try:
            box["v"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — surface the worker's error to the caller
            box["e"] = e
    t = threading.Thread(target=_run, name="turn", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TurnTimeout(f"turn exceeded {timeout_s:.0f}s (wedged exec/kernel — abandoned)")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def bootstrap_env() -> None:
    ef = Path(os.environ.get("ABA_LIVE_ENV", "/tmp/aba_8000.env"))
    if ef.exists():
        for kv in ef.read_bytes().split(b"\0"):
            if b"=" not in kv:
                continue
            k, v = kv.split(b"=", 1)
            try:
                k, v = k.decode(), v.decode()
            except Exception:
                continue
            if k in CRED_KEYS and not os.environ.get(k):
                os.environ[k] = v
    # Isolate the ENTIRE runtime under RUN so per-project dirs (project_data_dir,
    # artifacts, work, project.db) derive here — the agent's injected DATA_DIR then
    # points at OUR staged data, and the whole project state lands in the bundle.
    os.environ["ABA_RUNTIME_DIR"] = str(RUN)
    os.environ["ABA_DB_PATH"] = str(RUN / "live.db")
    # ENVS_DIR must be SHARED-FS, not node-local /tmp: a `requires: slurm` scenario that
    # provisions an overlay (pip) package installs it on the SUBMIT node, but the background
    # Slurm job runs on a DIFFERENT node — node-local /tmp is empty there → ModuleNotFoundError
    # (finding F6). A shared-FS cache also shares installs across nodes/runs. Kept outside
    # _runs/ so retention-pruning can't reap it, and gitignored, so still out of the bundle.
    os.environ.setdefault("ABA_ENVS_DIR", str(ROOT / "regtest" / ".envs_cache"))
    os.environ["ABA_TURN_LOG_DIR"] = str(RUN / "turnlog")
    os.environ["ABA_RAW_REQUEST_DIR"] = str(RUN / "rawreq")
    if os.environ.get("ABA_SCENARIO_MODEL"):
        os.environ["ABA_MODEL"] = os.environ["ABA_SCENARIO_MODEL"]
    sys.path.insert(0, str(ROOT / "backend"))


def stage_into(pid: str, items) -> None:
    """Copy data files AND subdirectories into the project's real data dir (what
    the agent's DATA_DIR resolves to) AND the global fallback. Subdir support
    matters: a folder-of-files dataset (e.g. an image set coloc/) must be staged
    WHOLE — a files-only copy left them out, so the agent saw no data and gave up."""
    from core.config import project_data_dir, DATA_DIR as GLOBAL_DATA
    dests = [Path(project_data_dir(pid)), Path(str(GLOBAL_DATA))]
    for d in dests:
        d.mkdir(parents=True, exist_ok=True)
    for f in items:
        f = Path(f)
        for d in dests:
            if f.is_dir():
                shutil.copytree(f, d / f.name, dirs_exist_ok=True)
            elif f.is_file():
                shutil.copy(f, d / f.name)


def discover_new_artifacts(client, pid, tid, seen: set) -> list:
    """Produced artifacts for a turn: kernel-path execs attach artifacts to the
    thread's `analysis` run (ana_*), not the SSE turn run — enumerate the thread's
    analysis runs and return artifacts not seen before (address exec_id:kind:idx)."""
    out = []
    ents = client.get("/api/entities", params={"project_id": pid, "include_archived": True}).json()
    ents = ents if isinstance(ents, list) else ents.get("entities", [])
    runs = [e["id"] for e in ents if e.get("type") == "analysis"
            and ((e.get("metadata") or {}).get("thread_id") in (None, tid))]
    for rid in runs:
        for a in client.get(f"/api/runs/{rid}/artifacts").json().get("artifacts", []):
            aid = a.get("artifact_id") or f"{a.get('exec_id')}:{a.get('kind')}:{a.get('idx')}"
            if aid not in seen:
                seen.add(aid); out.append(a)
    return out


# ---------- SSE ----------
def consume(stream, cap: dict) -> None:
    for line in stream.iter_lines():
        if not line or not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except Exception:
            continue
        t = ev.get("type")
        cap["kinds"][t] = cap["kinds"].get(t, 0) + 1
        if ev.get("run_id"):
            cap["run_id"] = ev["run_id"]
        if t == "delta":
            cap["text"].append(ev.get("text") or ev.get("delta") or "")
        elif t == "tool_start":
            cap["tools"].append(ev.get("name") or ev.get("tool") or "?")
        elif t == "tool_result":
            r = ev.get("result") or {}
            if isinstance(r, dict):
                if r.get("job_id"):                    # a backgrounded run_python/run_r job
                    cap.setdefault("jobs", []).append(r["job_id"])
                if r.get("returncode") not in (None, 0):
                    cap["tool_errors"].append({"tool": ev.get("name"), "rc": r.get("returncode"),
                                               "stderr": str(r.get("stderr", ""))[:400]})
        elif t == "usage":
            cap["usage"] = {k: ev.get(k) for k in ("input", "output", "cache_read", "cache_write")}
        elif t == "entity_registered":
            cap["entities"].append(ev.get("entity") or {})
        elif t in ("error", "cancelled"):
            cap["errors"].append(str(ev)[:400])


def drive_turn(client, pid, tid, text, resume_answer="Yes, go ahead.") -> dict:
    cap = {"run_id": None, "text": [], "tools": [], "entities": [], "usage": {},
           "kinds": {}, "tool_errors": [], "errors": [], "resume_hops": 0, "jobs": []}
    # H5: a read-timeout on the SSE stream so a turn that goes SILENT (wedged exec
    # emitting no events) unblocks iter_lines() on its own rather than leaking the
    # worker thread. Best-effort — the wall-clock call_with_timeout guard is the
    # hard backstop (TestClient's ASGI transport may not honor read timeouts).
    sse_to = max(60.0, TURN_TIMEOUT_S * 0.8)
    with client.stream("POST", "/api/chat", timeout=sse_to,
                       json={"text": text, "project_id": pid, "thread_id": tid}) as r:
        consume(r, cap)
    for _ in range(6):
        rid = cap["run_id"]
        if not rid:
            break
        try:
            st = client.get(f"/api/turns/{rid}").json().get("state")
        except Exception:
            break
        if st != "awaiting_user":
            break
        cap["resume_hops"] += 1
        with client.stream("POST", f"/api/turns/{rid}/resume", timeout=sse_to,
                           json={"user_text": resume_answer}) as r2:
            consume(r2, cap)
    cap["text"] = "".join(cap["text"]).strip()
    return cap


# ---------- raw request (context/cache) ----------
def new_request_files(seen: set) -> list[str]:
    cur = sorted(glob.glob(str(RUN / "rawreq" / "*.json")))
    fresh = [f for f in cur if f not in seen]
    seen.update(fresh)
    return fresh


def context_metrics(files: list[str]) -> dict:
    payloads = []
    for f in files:
        try:
            payloads.append(json.load(open(f)))
        except Exception:
            pass
    if not payloads:
        return {}
    last = payloads[-1]
    sysb = last.get("system") or []
    cc_sys = [bool(isinstance(b, dict) and b.get("cache_control")) for b in sysb]
    msgs = last.get("messages") or []
    last_cc = False
    if msgs and isinstance(msgs[-1].get("content"), list) and msgs[-1]["content"]:
        last_cc = bool(isinstance(msgs[-1]["content"][-1], dict)
                       and msgs[-1]["content"][-1].get("cache_control"))
    import hashlib
    stable = next((b.get("text", "") for b in sysb
                   if isinstance(b, dict) and b.get("cache_control")), "")
    return {"llm_calls": len(payloads), "n_msgs": len(msgs), "n_tools": len(last.get("tools") or []),
            "sys_cc_pattern": cc_sys, "last_msg_cc": last_cc,
            "sys_prefix_sha": hashlib.sha256(stable.encode()).hexdigest()[:12],
            "cache_breakpoints": (True in cc_sys) and last_cc,
            "req_files": [os.path.basename(f) for f in files]}


# ---------- selectors ----------
def resolve_target(tgt: dict, produced: dict, created: dict) -> dict:
    if not tgt:
        return {}
    if tgt.get("ref"):
        return dict(created.get(tgt["ref"]) or {})
    fs = tgt.get("from_step")
    arts = produced.get(fs, []) if fs else [a for v in produced.values() for a in v]
    kind = tgt.get("select")
    if kind:
        arts = [a for a in arts if a.get("kind") == kind]
    m = (tgt.get("match") or "").lower()
    if m:
        arts = [a for a in arts if m in (a.get("original_name", "") + a.get("url", "")).lower()]
    if not arts:
        return {}
    idx = tgt.get("index", "last")
    a = arts[-1] if idx in ("last", None) else arts[int(idx)]
    return {"artifact": a}


# ---------- background jobs (async: submitted this turn, finish later) ----------
def await_jobs(client, job_ids, timeout_s: float) -> list[dict]:
    """Poll each background job to a terminal state, then read its result from disk.
    Returns [{job_id, status, returncode, error, stdout, ok}] — `ok` = ran clean
    (status 'done', no error, returncode 0). Lets a scenario assert an async local/
    Slurm job actually SUCCEEDED in the right environment, not just that it was
    submitted (the prj_6d986f40 background-env-poisoning guard)."""
    # The AUTHORITATIVE completion signal is the job's result.json on disk (written by
    # slurm_entry when the code finishes) — the job-store `status` lags (poll-loop
    # reconciliation), so we don't gate on it. Break as soon as the result appears
    # (or the store reports a hard failure); `ok` is derived from the result.
    FAILED = {"failed", "cancelled", "cancel", "error"}
    out = []
    for jid in job_ids:
        status, deadline, res, done = None, time.time() + timeout_s, {}, False
        while time.time() < deadline:
            try:
                status = (client.get(f"/api/jobs/{jid}").json() or {}).get("status")
            except Exception:
                status = None
            for h in glob.glob(str(RUN / "**" / jid / "result.json"), recursive=True):
                try:
                    res = json.load(open(h)); done = True; break
                except Exception:
                    pass
            if done or status in FAILED:
                break
            time.sleep(3)
        rc, err = res.get("returncode"), res.get("error")
        out.append({"job_id": jid, "status": status, "returncode": rc, "error": err,
                    "stdout": res.get("stdout") or "",
                    "ok": done and err is None and rc in (None, 0)})
    return out


# ---------- mechanical checks ----------
def run_checks(step, cap, cmetrics, prev_msgs, client, pid, tid, created, produced_arts) -> list[str]:
    fails = []
    exp = step.get("expect") or {}
    txt = (cap.get("text") or "").lower()
    for m in (exp.get("must_mention") or []):
        if m.lower() not in txt:
            fails.append(f"missing_mention:{m!r}")
    for m in (exp.get("must_not") or []):
        if m.lower() in txt:
            fails.append(f"forbidden_present:{m!r}")
    for t in (exp.get("tools_used") or []):   # the agent actually invoked this tool this turn
        if t not in (cap.get("tools") or []):
            fails.append(f"tool_not_used:{t} (used={cap.get('tools')})")
    for t in (exp.get("tools_not_used") or []):   # advice/lightweight turns must NOT execute
        if t in (cap.get("tools") or []):
            fails.append(f"tool_used_unexpectedly:{t} (used={cap.get('tools')})")
    bj = exp.get("background_job")                 # await + assert an async job's OUTCOME
    if bj is not None:
        results = await_jobs(client, cap.get("jobs") or [],
                             float(os.environ.get("ABA_JOB_WAIT_S", "300")))
        if not results:
            fails.append("background_job: no background job was submitted this turn")
        else:
            summ = [{k: r.get(k) for k in ("job_id", "status", "returncode", "error")} for r in results]
            if bj.get("ok") and not any(r["ok"] for r in results):
                fails.append(f"background_job.ok: no job ran clean ({summ})")
            joined = " ".join((r.get("stdout") or "") for r in results).lower()
            for s in (bj.get("stdout_contains") or []):
                if s.lower() not in joined:
                    fails.append(f"background_job.stdout_contains:{s!r} (stdout={joined[:200]!r})")
            for s in (bj.get("stdout_absent") or []):
                if s.lower() in joined:
                    fails.append(f"background_job.stdout_absent:{s!r} present ({summ})")
    for k, n in (exp.get("produces") or {}).items():
        got = sum(1 for a in produced_arts if a.get("kind") == k)
        if got < n:
            fails.append(f"produces[{k}]>={n} but got {got}")
    st = exp.get("state") or {}
    man = json.dumps(client.get(f"/api/threads/{tid}/manifest").json(), default=str).lower()
    ents = client.get("/api/entities", params={"project_id": pid, "include_archived": True}).json()
    ents = ents if isinstance(ents, list) else ents.get("entities", [])
    for s in (st.get("manifest_contains") or []):
        if s.lower() not in man:
            fails.append(f"manifest_missing:{s!r}")
    for s in (st.get("manifest_not_contains") or []):
        if s.lower() in man:
            fails.append(f"manifest_has_forbidden:{s!r}")
    if "pinned_results_min" in st:
        nres = sum(1 for e in ents if e.get("type") == "result" and e.get("status") == "active")
        if nres < st["pinned_results_min"]:
            fails.append(f"pinned_results>={st['pinned_results_min']} but {nres}")
    for key, want_active in (("entity_active", True), ("entity_archived", False)):
        spec = st.get(key)
        if spec and spec.get("ref"):
            rec = created.get(spec["ref"]) or {}
            eid = rec.get("result_id") or rec.get("entity_id")
            e = next((x for x in ents if x.get("id") == eid), None)
            if not (e and ((e.get("status") == "active") == want_active)):
                fails.append(f"{key}:{spec['ref']} -> {e.get('status') if e else 'missing'}")
    for k, n in (st.get("entities_of_type") or {}).items():
        got = sum(1 for e in ents if e.get("type") == k and e.get("status") == "active")
        if got < n:
            fails.append(f"entities_of_type[{k}]>={n} but {got}")
    # --- provenance / versioning state ---
    if "reproduced" in st:   # checks THIS step's reproduce result (user-action `reproduce`)
        rr = (created.get(step["id"]) or {}).get("reproduce") or {}
        if bool(rr.get("reproduced")) != bool(st["reproduced"]):
            fails.append(f"reproduced={rr.get('reproduced')} expected {st['reproduced']} "
                         f"(err={str(rr.get('error'))[:80]})")
    if "env_drift" in st:
        rr = (created.get(step["id"]) or {}).get("reproduce") or {}
        if bool(rr.get("env_drift")) != bool(st["env_drift"]):
            fails.append(f"env_drift={rr.get('env_drift')} expected {st['env_drift']}")
    if st.get("superseded_min") is not None:   # a non-destructive revert hid newer versions
        nsup = sum(1 for e in ents if e.get("status") == "superseded")
        if nsup < st["superseded_min"]:
            fails.append(f"superseded>={st['superseded_min']} but {nsup}")
    if "revision_deleted" in st:   # THIS step's delete_revision result
        dr = (created.get(step["id"]) or {}).get("delete_revision") or {}
        if bool(dr.get("deleted")) != bool(st["revision_deleted"]):
            fails.append(f"revision_deleted={dr.get('deleted')} expected {st['revision_deleted']}")
    rv = st.get("revisions_min")   # {ref: sX, n: N}: the chain for that entity has >=N revisions
    if rv and rv.get("ref"):
        rec = created.get(rv["ref"]) or {}
        eid = rec.get("entity_id") or rec.get("result_id")
        chain = []
        if eid:
            try:
                chain = (client.get(f"/api/entities/{eid}/revisions").json() or {}).get("chain") or []
            except Exception:
                pass
        if len(chain) < (rv.get("n") or 0):
            fails.append(f"revisions[{rv['ref']}]>={rv.get('n')} but {len(chain)}")
    ctx = exp.get("context") or {}
    # NOTE: msgs_grow is NOT a hard gate. Empirically ABA does NOT monotonically
    # accumulate API messages — pre-resume it grows (11->30->45) but a resume
    # rehydrates a COMPACT, bounded context (~15-16) that stays flat. n_msgs is
    # recorded as telemetry (timeline/bundle); monotonic growth is an invalid
    # invariant for this platform, so we don't fail on it.
    if ctx.get("cache_breakpoints") and not cmetrics.get("cache_breakpoints"):
        fails.append(f"cache_breakpoints absent: sys={cmetrics.get('sys_cc_pattern')} last={cmetrics.get('last_msg_cc')}")
    if ctx.get("cache_read") and not (cap.get("usage", {}).get("cache_read") or 0) > 0:
        fails.append("cache_read=0 (no cache hit)")
    return fails


# ---------- LLM-judge (vision rubric) ----------
def evidence_trail(files: list[str], max_chars: int = 4500) -> str:
    """The tool outputs the agent ACTUALLY computed this session, pulled from the
    last raw request's messages — so the judge can tell RECALL (values traceable
    here) from FABRICATION. Prioritises tool_use(code) + tool_result blocks; keeps
    the most recent (tail)."""
    payload = None
    for f in files or []:
        try:
            payload = json.load(open(f))
        except Exception:
            pass
    if not payload:
        return "(no evidence trail captured)"
    bits = []
    for m in payload.get("messages") or []:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                bits.append(f"[ran {b.get('name')}] {json.dumps(b.get('input') or {})[:280]}")
            elif b.get("type") == "tool_result":
                rc = b.get("content")
                rc = rc if isinstance(rc, str) else json.dumps(rc, default=str)
                bits.append(f"[output] {rc[:500]}")
    trail = "\n".join(bits)
    return trail[-max_chars:] if len(trail) > max_chars else trail


def judge_step(spec, step, cap, produced_arts, client, evidence="") -> dict:
    """Grade ONE agent step against planted truth + the step's checks, seeing the
    produced figures (vision) AND the session's tool-output evidence trail (so
    recalled-but-real values aren't misread as fabrication). Multi-dim rubric."""
    from core.llm import sync_anthropic_client, _wants_cc_marker, _CC_MARKER_BLOCK
    exp = step.get("expect") or {}
    eo = spec.get("expected_overall") or {}
    head = (
        f"Scenario: {spec.get('title')} ({spec.get('domain')})\n"
        f"Planted truth (ground truth built into the data):\n{eo.get('planted_truth', '')}\n\n"
        f"STEP {step['id']} (kind={step.get('kind')}): the user asked:\n{step.get('prompt', '')}\n\n"
        f"What a correct response looks like:\n{exp.get('checks') or eo.get('notes', '')}\n\n"
        f"The agent's reply:\n{cap.get('text', '')[:6000]}\n\n"
        f"Tools the agent used: {cap.get('tools')}. Tool errors: {cap.get('tool_errors')}.\n\n"
        f"EVIDENCE TRAIL — tool outputs the agent computed this session (a value/claim that "
        f"TRACES to this, or to a figure it made, is NOT fabrication even if recalled from a "
        f"prior turn):\n{evidence}\n\n"
        "Grade how well the agent handled THIS step. Each dimension 0-3 (3=ideal, 0=wrong/absent; "
        "use null for a dimension that does not apply, e.g. lifecycle on a plain analysis step):\n"
        "- correctness: scientifically right vs the planted truth\n"
        "- completeness: addressed what was asked\n"
        "- no_fabrication: 3=no invented specifics; numbers/claims that TRACE to the evidence trail "
        "or to a produced figure are NOT fabrication even if recalled from a prior turn; "
        "0=specifics with no basis in the data/evidence\n"
        "- lifecycle: handled branch/revise/resume/delete/version_change correctly (reused prior state etc.)\n"
        "- efficiency: solved without excessive flailing/retries\n"
        "Also: overall (0-3), fabrication_detected (bool), friction ('none'|'minor'|'major'), "
        "and a one-paragraph rationale citing specifics. Respond with ONLY a JSON object with keys: "
        "correctness, completeness, no_fabrication, lifecycle, efficiency, overall, "
        "fabrication_detected, friction, rationale."
    )
    blocks = [{"type": "text", "text": head}]
    for a in [x for x in produced_arts if x.get("kind") == "figure"][:3]:
        try:
            img = client.get(a["url"]).content
            blocks.append({"type": "text", "text": f"Figure produced: {a.get('original_name')}"})
            blocks.append({"type": "image", "source": {"type": "base64",
                          "media_type": "image/png", "data": base64.b64encode(img).decode()}})
        except Exception:
            pass
    system = [{"type": "text", "text": "You are a rigorous bioinformatics test grader. "
               "Be skeptical; reward correctness and penalise fabrication. Output ONLY JSON."}]
    if _wants_cc_marker():
        system = [_CC_MARKER_BLOCK, *system]
    try:
        msg = sync_anthropic_client().messages.create(
            model=JUDGE_MODEL, max_tokens=1200, system=system,
            messages=[{"role": "user", "content": blocks}])
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        s = raw[raw.find("{"): raw.rfind("}") + 1]
        return json.loads(s)
    except Exception as e:  # noqa: BLE001 — judging must never crash the run
        return {"judge_error": f"{type(e).__name__}: {e}", "raw": locals().get("raw", "")[:300]}


def _rubric_overall(j: dict):
    if not j or j.get("judge_error"):
        return None
    v = j.get("overall")
    return v if isinstance(v, (int, float)) else None


# ---------- main ----------
def main() -> int:
    bootstrap_env()
    spec = yaml.safe_load((LIB / SCENARIO / "scenario.yaml").read_text())
    if not spec.get("steps"):
        print(f"{SCENARIO} is v1 (no steps) — use run_scenario_library.py"); return 2
    # A scenario can require a specific submitter (e.g. `requires: slurm` to exercise
    # the real job.sh module-load path). Skip cleanly when it's not the active one —
    # a local-submitter background job wouldn't test what the scenario is guarding.
    req = (spec.get("requires") or "").strip().lower()
    if req == "slurm":
        from core.jobs.submitter import submitter_name
        if submitter_name() != "slurm":
            print(f"[skip] {SCENARIO} requires the Slurm submitter (set ABA_BATCH_SUBMITTER=slurm); "
                  f"active submitter is '{submitter_name()}'."); return 0
    src = LIB / SCENARIO / "data"
    # Scenario data/ is generated, not committed (see regtest/scenarios/_regen_all.sh).
    # On a fresh clone it won't exist yet — point the user at the regen step instead
    # of a confusing empty-DATA_DIR run. Non-fatal (some scenarios fetch their data).
    if not src.is_dir() or not any(src.iterdir()):
        print(f"[note] {SCENARIO}/data is empty — if this scenario uses local data, run "
              f"`bash regtest/scenarios/_regen_all.sh` first (data is generated, not committed).")

    import content.bio  # noqa
    import content.bio.lifecycle.registry  # noqa
    from core.graph._schema import init_db
    init_db()
    from fastapi.testclient import TestClient
    from main import app

    mode = "SCIENCE" if os.environ.get("ABA_SCENARIO_MODEL") else "HARNESS-STRESS"
    print(f"=== v2 SCENARIO: {SCENARIO} ({spec.get('domain')}) — {len(spec['steps'])} steps ===")
    print(f"    mode: {mode}  agent_model: {os.environ.get('ABA_MODEL')}  judge: {JUDGE_MODEL if DO_JUDGE else 'off'}")
    print(f"    bundle: {RUN}\n")

    produced, created, seen_artifacts, seen_reqs = {}, {}, set(), set()
    timeline, report, bundle_steps = [], [], []
    prev_msgs = None
    pid = tid = None
    client_cm = TestClient(app)
    client = client_cm.__enter__()
    try:
        pid = client.post("/api/projects", json={"name": SCENARIO}).json().get("id", "single")
        client.post(f"/api/projects/{pid}/open")
        tid = client.post("/api/threads", json={"project_id": pid, "title": SCENARIO}).json().get("id")
        if src.is_dir():
            stage_into(pid, list(src.iterdir()))   # files AND subdirs (e.g. coloc/)

        def restart_client():
            nonlocal client, client_cm
            try:
                client_cm.__exit__(None, None, None)
            except Exception:
                pass
            client_cm = TestClient(app)
            client = client_cm.__enter__()
            client.post(f"/api/projects/{pid}/open")

        for step in spec["steps"]:
            sid, kind, actor = step["id"], step.get("kind", "analyze"), step.get("actor", "agent")
            try:
                staged = [LIB / SCENARIO / f for f in (step.get("stage") or [])]
                if staged:
                    stage_into(pid, staged)
                if kind == "resume":
                    restart_client()
                    print(f"[{sid}] (resume) re-attached to DB")

                cap, cmetrics, judged = {}, {}, None
                # H8: a pure state step (e.g. `resume` with no prompt) defaults to
                # actor=agent but has nothing to send — re-attach already happened
                # above, so skip the turn instead of KeyError'ing on step["prompt"].
                if actor == "agent" and not step.get("prompt"):
                    print(f"[{sid}] {kind}/agent  (no prompt — state-only step, no turn)")
                elif actor == "agent":
                    if step.get("new_thread"):
                        tid = client.post("/api/threads", json={"project_id": pid,
                                          "title": f"{SCENARIO}:{sid}"}).json().get("id")
                    cap = call_with_timeout(drive_turn, TURN_TIMEOUT_S, client, pid, tid,
                                            step["prompt"],
                                            resume_answer=step.get("resume_answer", "Yes, go ahead."))
                    _reqf = new_request_files(seen_reqs)
                    cmetrics = context_metrics(_reqf)
                    produced[sid] = discover_new_artifacts(client, pid, tid, seen_artifacts)
                    u = cap.get("usage", {})
                    timeline.append({"step": sid, "kind": kind, "n_msgs": cmetrics.get("n_msgs"),
                                     "llm_calls": cmetrics.get("llm_calls"),
                                     "cache_read": u.get("cache_read"), "cache_write": u.get("cache_write")})
                    nfig = sum(1 for a in produced.get(sid, []) if a.get("kind") == "figure")
                    print(f"[{sid}] {kind}/agent  msgs={cmetrics.get('n_msgs')} calls={cmetrics.get('llm_calls')} "
                          f"cache_r={u.get('cache_read')} figs={nfig} tools={cap.get('tools')}"
                          f"{' ERR' if cap.get('tool_errors') or cap.get('errors') else ''}")
                    print(f"      🗣 {cap['text'][:140]}")
                    if DO_JUDGE:
                        judged = judge_step(spec, step, cap, produced.get(sid, []), client,
                                            evidence=evidence_trail(_reqf))
                        ov = _rubric_overall(judged)
                        print(f"      ⚖ judge: overall={ov} "
                              f"{ {d: judged.get(d) for d in RUBRIC_DIMS} if ov is not None else judged.get('judge_error')}")
                else:
                    tg = resolve_target(step.get("target") or {}, produced, created)
                    art = tg.get("artifact")
                    try:
                        if kind == "pin" and art:
                            out = client.post(f"/api/artifacts/{art['exec_id']}/{art['kind']}/{art['idx']}/pin",
                                              json={"title": (step.get("target") or {}).get("title")}).json()
                            created[sid] = {"entity_id": out.get("entity_id"), "result_id": out.get("result_id")}
                            print(f"[{sid}] {kind}/user  pinned {art.get('original_name')} -> result={out.get('result_id')}")
                        elif kind in ("delete", "drop"):
                            # H6: a user `drop` is a soft-archive (discard a pinned
                            # Result, restorable) — same route as a soft `delete`. It
                            # used to fall through to the else: SKIP, so the entity was
                            # never archived and the downstream entity_archived check
                            # failed for a harness reason, not a platform one (microbiome s11).
                            eid = tg.get("result_id") or tg.get("entity_id")
                            if not eid:
                                print(f"[{sid}] {kind}/user  unresolved target={step.get('target')}")
                            else:
                                suffix = "?hard=true&cascade=members" if step.get("hard") else ""
                                rc = client.delete(f"/api/entities/{eid}{suffix}").status_code
                                created[sid] = tg
                                print(f"[{sid}] {kind}/user  {'hard-delete' if step.get('hard') else 'archive'} {eid} -> {rc}")
                        elif kind == "restore":
                            eid = tg.get("result_id") or tg.get("entity_id")
                            client.post(f"/api/entities/{eid}/restore"); created[sid] = tg
                            print(f"[{sid}] {kind}/user  restored {eid}")
                        elif kind == "unpin":
                            eid = tg.get("entity_id") or (art or {}).get("artifact_id")
                            client.post(f"/api/entities/{eid}/unpin"); created[sid] = tg
                            print(f"[{sid}] {kind}/user  unpinned {eid}")
                        elif kind == "modify_figure" and tg.get("entity_id") and step.get("modified_code"):
                            out = client.post(f"/api/entities/{tg['entity_id']}/make_revision",
                                              json={"modified_code": step["modified_code"]}).json()
                            # H7: the HTTP route returns the new id at out["entity"]["id"],
                            # NOT out["new_entity_id"] (that's the lifecycle fn's key). Reading
                            # the wrong key gave entity_id=None, so CHAINED modify_figure steps
                            # SKIPped (version_revert s5/s6). Also surface a 400 (supersede guard).
                            new_eid = (out.get("entity") or {}).get("id") or out.get("new_entity_id")
                            created[sid] = {"entity_id": new_eid}
                            print(f"[{sid}] {kind}/user  revised -> {new_eid}"
                                  f"{'  ERR:'+str(out.get('detail'))[:90] if not new_eid else ''}")
                        elif kind == "reproduce":
                            # Provenance: re-run the exec that produced this entity in
                            # the CURRENT env and report (reproduced / env_drift /
                            # produced). Does NOT create a new entity. Prefer the
                            # figure entity_id (carries exec_id) over the result wrapper.
                            eid = tg.get("entity_id") or tg.get("result_id")
                            if not eid:
                                print(f"[{sid}] {kind}/user  unresolved target={step.get('target')}")
                            else:
                                out = client.post(f"/api/entities/{eid}/reproduce").json()
                                created[sid] = {**tg, "reproduce": out}
                                print(f"[{sid}] {kind}/user  {eid} -> reproduced={out.get('reproduced')} "
                                      f"drift={out.get('env_drift')} produced={len(out.get('produced') or [])}"
                                      f"{' ERR:'+str(out.get('error'))[:80] if out.get('error') else ''}")
                        elif kind == "delete_revision":
                            # Hard-delete ONE revision from the chain (tests re-parent +
                            # member re-anchor), distinct from soft-archiving an entity.
                            eid = tg.get("entity_id") or tg.get("result_id")
                            if not eid:
                                print(f"[{sid}] {kind}/user  unresolved target={step.get('target')}")
                            else:
                                out = client.post(f"/api/entities/{eid}/delete-revision").json()
                                created[sid] = {**tg, "delete_revision": out}
                                print(f"[{sid}] {kind}/user  {eid} -> new_anchor={out.get('new_anchor')} "
                                      f"re_parented={len(out.get('re_parented_children') or [])}")
                        else:
                            print(f"[{sid}] {kind}/user  unresolved/SKIP target={step.get('target')}")
                    except Exception as e:  # noqa: BLE001
                        print(f"[{sid}] {kind}/user  ERROR {type(e).__name__}: {e}")

                fails = run_checks(step, cap, cmetrics, prev_msgs, client, pid, tid, created, produced.get(sid, []))
                if actor == "agent" and cmetrics.get("n_msgs"):
                    prev_msgs = cmetrics["n_msgs"]
                verdict = "PASS" if not fails else "FAIL"
                print(f"      [{verdict}] {('; '.join(fails)) if fails else 'all checks ok'}\n")
                report.append({"step": sid, "kind": kind, "actor": actor, "verdict": verdict,
                               "fails": fails, "rubric": judged})
                bundle_steps.append({
                    "step": sid, "kind": kind, "actor": actor, "verdict": verdict, "fails": fails,
                    "prompt": step.get("prompt"), "response": cap.get("text"),
                    "tools": cap.get("tools"), "tool_errors": cap.get("tool_errors"),
                    "errors": cap.get("errors"), "resume_hops": cap.get("resume_hops"),
                    "usage": cap.get("usage"), "context": cmetrics,
                    "produced": [{"kind": a.get("kind"), "name": a.get("original_name"),
                                  "address": a.get("artifact_id")} for a in produced.get(sid, [])],
                    "rubric": judged,
                })
            except Exception as e:  # noqa: BLE001 — a turn/kernel crash must not void the run or the bundle
                import traceback as _tb
                err = f"{type(e).__name__}: {e}"
                print(f"[{sid}] CRASHED: {err} — recording + restarting session\n")
                report.append({"step": sid, "kind": kind, "actor": actor, "verdict": "FAIL",
                               "fails": [f"step_crash:{err}"], "rubric": None})
                bundle_steps.append({"step": sid, "kind": kind, "actor": actor, "verdict": "FAIL",
                                     "fails": [f"step_crash:{err}"], "crash": _tb.format_exc()[-2000:]})
                try:
                    restart_client()
                except Exception:
                    pass
    finally:
        try:
            client_cm.__exit__(None, None, None)
        except Exception:
            pass

    # ---- aggregate + persist ----
    npass = sum(1 for r in report if r["verdict"] == "PASS")
    rubrics = [r["rubric"] for r in report if r.get("rubric") and not r["rubric"].get("judge_error")]
    def _mean(dim):
        vals = [r.get(dim) for r in rubrics if isinstance(r.get(dim), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None
    rubric_summary = {d: _mean(d) for d in (*RUBRIC_DIMS, "overall")}
    summary = {"scenario": SCENARIO, "mode": mode, "agent_model": os.environ.get("ABA_MODEL"),
               "judge_model": JUDGE_MODEL if DO_JUDGE else None,
               "mechanical": {"pass": npass, "total": len(report)},
               "rubric_mean": rubric_summary, "report": report, "timeline": timeline}
    (RUN / "report.json").write_text(json.dumps(summary, indent=2, default=str))
    (RUN / "bundle.json").write_text(json.dumps(
        {"scenario": SCENARIO, "mode": mode, "agent_model": os.environ.get("ABA_MODEL"),
         "pid": pid, "tid": tid, "steps": bundle_steps,
         "dirs": {"raw_requests": "rawreq/", "turn_contexts": "turnlog/", "project": "projects/"}},
        indent=2, default=str))

    print("=== context / cache timeline (agent turns) ===")
    for t in timeline:
        print(f"  {t['step']:7s} msgs={t['n_msgs']} llm_calls={t['llm_calls']} "
              f"cache_read={t['cache_read']} cache_write={t['cache_write']}")
    print(f"\n=== {SCENARIO} [{mode}] ===")
    print(f"  mechanical: {npass}/{len(report)} steps PASS")
    print(f"  rubric mean: {rubric_summary}")
    print(f"  bundle: {RUN}")
    return 0 if npass == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
