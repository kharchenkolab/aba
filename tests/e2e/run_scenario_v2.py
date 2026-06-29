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

Forensic bundle (kept, NOT checked in): misc/scenarios/_runs/<scenario>-<ts>/ holds the
replayable raw requests, turn-context sidecars, the per-step transcript+verdict+judge,
the project DB + artifacts, and bundle.json (step -> files navigation) for a later
Opus-1M forensic agent to root-cause failures.

    ABA_SCENARIO=_selftest_session \
      /home/pkharchenko/aba/aba_runtime/.venv/bin/python -u tests/e2e/run_scenario_v2.py
"""
from __future__ import annotations
import base64
import glob
import json
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "misc" / "scenarios"
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
    os.environ.setdefault("ABA_ENVS_DIR", "/tmp/aba_discovery/envs")  # cached installs, NOT in bundle
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
            if isinstance(r, dict) and r.get("returncode") not in (None, 0):
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
           "kinds": {}, "tool_errors": [], "errors": [], "resume_hops": 0}
    with client.stream("POST", "/api/chat",
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
        with client.stream("POST", f"/api/turns/{rid}/resume",
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
    src = LIB / SCENARIO / "data"

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
                if actor == "agent":
                    if step.get("new_thread"):
                        tid = client.post("/api/threads", json={"project_id": pid,
                                          "title": f"{SCENARIO}:{sid}"}).json().get("id")
                    cap = drive_turn(client, pid, tid, step["prompt"],
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
                        elif kind == "delete":
                            eid = tg.get("result_id") or tg.get("entity_id")
                            suffix = "?hard=true&cascade=members" if step.get("hard") else ""
                            rc = client.delete(f"/api/entities/{eid}{suffix}").status_code
                            created[sid] = tg
                            print(f"[{sid}] {kind}/user  delete {eid} -> {rc}")
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
                            created[sid] = {"entity_id": out.get("new_entity_id")}
                            print(f"[{sid}] {kind}/user  revised -> {out.get('new_entity_id')}")
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
