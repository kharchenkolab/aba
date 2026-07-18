"""Dataset-management live-agent study (misc/datasets2.md; D5-L1).

Drives REAL agent turns (/api/chat → guide → Anthropic via the deployment's
OAuth) with REAL execution — no stubs: a live weft substrate in a THROWAWAY
ABA_HOME (oauth.json + installation symlinked from the real install), a real
remote site (mendel over ssh, disposable dirs, cleaned up), and a local http
"portal". Captures every tool call + the agent's text per scenario, asserts
the weft-native dataset behaviors, and writes full transcripts for review.

Run:  python regtest/datasets/study.py [--only name,name]
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL = Path(os.environ.get("ABA_INSTALL") or os.environ.get("ABA_HOME")
               or (Path.home() / ".aba"))

R_DATA = "/home/pkharchenko/aba-dstest-data2/cohort"     # disposable, on mendel


# ── 1. creds + env, then ISOLATE into a throwaway home ───────────────────────
def _load_config_env() -> None:
    cfg = INSTALL / "config.env"
    if not cfg.exists():
        return
    for line in cfg.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_config_env()
_tmp = Path(tempfile.mkdtemp(prefix="aba_dstudy_"))
HOME = _tmp / "home"
HOME.mkdir(parents=True)
# oauth: SYMLINK (refreshes write through — no token-rotation divergence
# against the real store) — but ONLY while the store is actually fresh: an
# expired store whose refresh 400s POISONS the resolver (tier-1 failure does
# not fall through), so we skip it and bridge via CLAUDE_CODE_OAUTH_TOKEN
# from the Claude Code CLI's own credential (macOS Keychain), touching
# nothing of the user's store. installation: the recipe/skill bundle, RO.
import subprocess as _sp
import time as _time


def _store_fresh() -> bool:
    try:
        d = json.load(open(INSTALL / "oauth.json"))
        return (d.get("expires_at") or 0) > _time.time() + 120
    except Exception:  # noqa: BLE001
        return False


for name in ("oauth.json", "installation"):
    src = INSTALL / name
    if not src.exists():
        continue
    if name == "oauth.json" and not _store_fresh():
        r = _sp.run(["security", "find-generic-password",
                     "-s", "Claude Code-credentials", "-w"],
                    capture_output=True, text=True)
        tok = ""
        if r.returncode == 0:
            try:
                tok = (json.loads(r.stdout).get("claudeAiOauth") or {})                     .get("accessToken") or ""
            except Exception:  # noqa: BLE001
                pass
        if tok:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = tok
            print("[study] aba oauth store is stale — bridging via the "
                  "Claude Code CLI credential (keychain)")
            continue
        sys.exit("[study] aba oauth store is stale and no CLI credential "
                 "found — re-login aba, then rerun")
    os.symlink(src, HOME / name)
os.environ["ABA_HOME"] = str(HOME)
os.environ["ABA_RUNTIME_DIR"] = str(_tmp / "runtime")
os.environ["ABA_DB_PATH"] = str(_tmp / "study.db")
os.environ["ABA_RAW_REQUEST_DIR"] = str(_tmp / "rawreq")
RUN_OUT = _tmp / "transcripts"
RUN_OUT.mkdir()
print(f"[study] throwaway home: {HOME}\n[study] transcripts: {RUN_OUT}")

sys.path.insert(0, str(REPO / "backend"))

import content.bio  # noqa: E402,F401
from core.graph._schema import init_db  # noqa: E402

init_db()
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from core.graph.entities import find_entities, get_entity  # noqa: E402


def ssh(cmd: str):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "mendel", cmd],
                          capture_output=True, text=True, timeout=120)


# ── 2. the portal (local http) ────────────────────────────────────────────────
www = _tmp / "www"
www.mkdir()
(www / "table.csv").write_text("id,value\n" + "\n".join(
    f"{i},{i * 3 % 17}" for i in range(200)) + "\n")


class _H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(www), **k)

    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/table.csv"


# ── 3. drive one turn, capture tools + text ──────────────────────────────────
def _consume_stream(cap, r):
    """Shared SSE consumption for /api/chat AND /api/turns/…/resume."""
    for line in r.iter_lines():
        if not line or not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except Exception:  # noqa: BLE001
            continue
        t = ev.get("type")
        if t == "delta":
            cap["text"].append(ev.get("text") or ev.get("delta") or "")
        elif t == "manifest":
            cap["run_id"] = ev.get("run_id")     # the resume handle
        elif t == "plan":
            cap["plan"] = {"entity_id": ev.get("entity_id"),
                           "title": ev.get("title")}
        elif t == "tool_start":
            cap["tools"].append({"name": ev.get("name") or ev.get("tool"),
                                 "input": ev.get("input") or {},
                                 "tool_use_id": ev.get("tool_use_id")})
        elif t == "tool_result":
            # pair the envelope back onto its call — assertions can then
            # distinguish "was invoked" from "succeeded"
            for tc in reversed(cap["tools"]):
                if tc.get("tool_use_id") == ev.get("tool_use_id"):
                    tc["result"] = (ev.get("result")
                                    if isinstance(ev.get("result"), dict) else {})
                    break
        elif t in ("error", "notice"):
            cap.setdefault("errors", []).append(
                {"type": t, "text": ev.get("text"),
                 "detail": ev.get("detail")})
            print(f"    [turn {t}] {ev.get('text')!r} {ev.get('detail')!r}"[:200])


def drive_turn(client, pid, tid, text, timeout_s=900):
    cap = {"prompt": text, "tools": [], "text": []}
    with client.stream("POST", "/api/chat",
                       json={"text": text, "project_id": pid,
                             "thread_id": tid}) as r:
        _consume_stream(cap, r)
    cap["text"] = "".join(cap["text"]).strip()
    return cap


def resume_turn(client, pid, cap_or_run_id, text="", action=None):
    """Drive the REAL approval path: resume an awaiting_user turn (plan
    Go/Adjust, approval gates) via /api/turns/{run_id}/resume — the same
    endpoint the UI's Go button posts to."""
    rid = (cap_or_run_id.get("run_id")
           if isinstance(cap_or_run_id, dict) else cap_or_run_id)
    cap = {"prompt": f"[resume] {text or action or 'go'}",
           "tools": [], "text": []}
    body = {"user_text": text, "project_id": pid}
    if action:
        body["action"] = action
    with client.stream("POST", f"/api/turns/{rid}/resume", json=body) as r:
        # a 409 (nothing awaiting resume) is returned as a NORMAL response —
        # without this check the caller's "approval drove execution" assertion
        # passes with the resume path never exercised (recheck finding)
        if r.status_code >= 400:
            raise RuntimeError(f"resume of {rid} → HTTP {r.status_code} "
                               f"(no turn awaiting resume?)")
        _consume_stream(cap, r)
    cap["text"] = "".join(cap["text"]).strip()
    return cap


def tools_named(caps, name):
    return [t for t in sum((c["tools"] for c in caps), [])
            if t["name"] == name]


def all_text(caps):
    return "\n".join(c["text"] for c in caps)


def dataset_by_title(frag):
    rows = find_entities(type="dataset", not_deleted=True)
    for r in rows:
        if frag.lower() in (r.get("title") or "").lower():
            return get_entity(r["id"])
    return None


RESULTS = []


def scenario(name):
    def deco(fn):
        fn._scenario = name
        return fn
    return deco


_TRUTH_SEEN: set = set()


def verify_jobs_truth() -> list:
    """Global invariant, swept after EVERY scenario: each weft job row must
    agree with the substrate. A task the substrate finished cleanly may never
    read failed (the false-'infra failure' class, misc/bug1.md P0), and no row
    may sit done-with-error (double-finalize residue). Runs across all project
    DBs, so it catches false verdicts from ANY scenario — including ones whose
    own assertions never look at jobs. Dedupes across sweeps."""
    import sqlite3
    from core.config import PROJECTS_DIR
    from core.compute import adapter as ad
    out = []
    try:
        comp = ad.get_compute()
    except Exception:  # noqa: BLE001 — substrate gone: nothing to compare against
        return out
    # enumerate every DB that can hold job rows. The studies run with
    # ABA_DB_PATH set → SINGLE mode → jobs live in the ONE flat DB and the
    # PROJECTS_DIR walk sees nothing: without this branch the sweep was
    # VACUOUSLY clean in every study (zero rows examined — the exact
    # false-ALL-PASS class this harness exists to kill; found by
    # restart_study, 2026-07)
    dbs = []
    from core import projects as _projects
    if _projects.SINGLE:
        from core.config import settings as _settings
        p = Path(str(_settings.db_path.get()))
        if p.exists():
            dbs.append(p)
    elif PROJECTS_DIR.exists():
        dbs = [proj / "project.db" for proj in sorted(PROJECTS_DIR.iterdir())
               if proj.is_dir() and (proj / "project.db").exists()]
    all_rows = []
    for db in dbs:
        try:
            c = sqlite3.connect(db); c.row_factory = sqlite3.Row
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='jobs'").fetchone():
                c.close(); continue
            all_rows += c.execute(
                "SELECT id,status,error,params FROM jobs").fetchall()
            c.close()
        except sqlite3.Error:
            continue
    for r in all_rows:
        try:
            p = json.loads(r["params"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        wid = p.get("weft_id")
        if p.get("submitter") != "weft" or not wid:
            continue
        if r["status"] == "done" and (r["error"] or "").strip():
            out.append(f"{r['id']}: done row carries an error "
                       f"(double-finalize residue): {r['error'][:100]}")
        try:
            st = comp.sync_call("task_status", wid)
            state = st[0]["state"] if st else None
        except Exception:  # noqa: BLE001
            continue
        if state == "DONE" and r["status"] == "failed":
            exit0 = True
            try:
                exit0 = (comp.sync_call("task_result", wid)
                         .get("exit_code") in (0, None))
            except Exception:  # noqa: BLE001
                pass
            if exit0:
                out.append(f"{r['id']}: substrate DONE/exit 0 but row "
                           f"FAILED: {(r['error'] or '')[:100]}")
    fresh = [v for v in out if v not in _TRUTH_SEEN]
    _TRUTH_SEEN.update(out)
    return fresh


def run_scenario(client, name, fn):
    pid = client.post("/api/projects", json={"name": f"ds-{name}"}).json()["id"]
    client.post(f"/api/projects/{pid}/open")
    tid = client.post("/api/threads",
                      json={"project_id": pid, "title": name}).json()["id"]
    t0 = time.time()
    try:
        caps, checks = fn(client, pid, tid)
        checks = list(checks)
        # append the global truth sweep to EVERY scenario's verdict
        violations = verify_jobs_truth()
        if violations:
            checks += [(f"truth-sweep: {v}", False) for v in violations]
        else:
            checks.append(("jobs-vs-substrate truth sweep clean", True))
        ok = all(v for _, v in checks)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        caps, checks, ok = [], [("exception", False)], False
    dt = time.time() - t0
    (RUN_OUT / f"{name}.json").write_text(json.dumps(
        {"name": name, "seconds": round(dt, 1),
         "checks": [[c, bool(v)] for c, v in checks],
         "turns": caps}, indent=1, default=str))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} ({dt:.0f}s)")
    for c, v in checks:
        print(f"    {'✓' if v else '✗'} {c}")
    RESULTS.append((name, ok))


# ── scenarios ─────────────────────────────────────────────────────────────────

@scenario("url_register")
def s_url(client, pid, tid):
    cap = drive_turn(client, pid, tid,
                     f"Please register the file at {URL} as a dataset called "
                     f"'Portal Table'. Just register it — no analysis.")
    caps = [cap]
    reg = tools_named(caps, "register_dataset")
    ent = dataset_by_title("Portal Table")
    md = (ent or {}).get("metadata") or {}
    hand_dl = any("urlretrieve" in (t["input"].get("code") or "")
                  or "requests.get" in (t["input"].get("code") or "")
                  for t in tools_named(caps, "run_python"))
    return caps, [
        ("register_dataset called with url=", any(t["input"].get("url") == URL
                                                  for t in reg)),
        ("no hand-rolled download in run_python", not hand_dl),
        ("entity exists with a content ref", bool(md.get("ref", "").startswith("dref:"))),
        ("source_key recorded", md.get("source_key") == URL),
    ]


@scenario("url_reuse")
def s_reuse(client, pid, tid):
    """Dedup on re-register. SELF-CONTAINED (vacuity review): the old global
    count-==-1 could never fail standalone (the single registration trivially
    yields 1) and rode url_register's leftovers otherwise — now the scenario
    lays its own baseline and asserts the RE-register minted nothing."""
    def _n():
        return len([r for r in find_entities(type="dataset", not_deleted=True)
                    if ((get_entity(r["id"]) or {}).get("metadata") or {})
                    .get("source_key") == URL])
    caps = []
    if _n() == 0:          # standalone: create the baseline registration first
        caps.append(drive_turn(client, pid, tid,
                    f"Register {URL} as a dataset named 'Portal Table'."))
    n_before = _n()
    caps.append(drive_turn(client, pid, tid,
                f"Register {URL} as a dataset named 'Portal Table Again'."))
    n_after = _n()
    txt = caps[-1]["text"].lower()      # judge the REUSE turn only
    return caps, [
        ("baseline registration exists", n_before >= 1),
        ("the re-register did NOT mint a new dataset", n_after == n_before),
        ("agent says it's already registered",
         "already" in txt or "existing" in txt or "reus" in txt),
    ]


@scenario("remote_inplace")
def s_remote(client, pid, tid):
    ssh(f"mkdir -p {R_DATA} && head -c 30000000 /dev/urandom > {R_DATA}/a.bin"
        f" && echo hdr > {R_DATA}/readme.txt")
    cap = drive_turn(client, pid, tid,
                     f"Our cohort data lives on the machine 'mendel' at "
                     f"{R_DATA}. Register it as dataset 'Mendel Cohort'. "
                     f"It is large — it must NOT be copied off that machine.")
    caps = [cap]
    reg = tools_named(caps, "register_dataset")
    ent = dataset_by_title("Mendel Cohort")
    md = (ent or {}).get("metadata") or {}
    copied = any(k in (t["input"].get("code") or "")
                 for t in tools_named(caps, "run_python")
                 for k in ("scp", "rsync", "sftp"))
    return caps, [
        ("register_dataset called with site=mendel",
         any((t["input"].get("site") or "").startswith("mendel") for t in reg)),
        ("durable home recorded on the entity",
         (md.get("home") or {}).get("path") == R_DATA),
        ("no content ref yet (lazy identity)", md.get("ref") is None),
        ("descriptor shows the true size",
         (md.get("descriptor") or {}).get("total_bytes") == 30_000_004),
        ("no copy attempted", not copied),
    ]


@scenario("drift_and_missing")
def s_drift(client, pid, tid):
    # own path — sharing R_DATA with remote_inplace trips the SOURCE-KEY
    # DEDUP (by design!) and reuses that scenario's entity (found live in
    # the full-sequence run: register returned already_registered)
    drift_data = R_DATA + "-drift"
    ssh(f"mkdir -p {drift_data} && head -c 1000 /dev/urandom > {drift_data}/a.bin")
    # the agent registers it (a direct tool call would bind outside the
    # request's project and be invisible to the agent — found live)
    cap0 = drive_turn(client, pid, tid,
                      f"Register the data at {drift_data} on mendel as dataset "
                      f"'Drift Cohort' (in place, no copying).")
    assert dataset_by_title("Drift Cohort"), cap0["text"][:300]
    ssh(f"echo extra >> {drift_data}/a.bin")
    cap1 = drive_turn(client, pid, tid,
                      "Before we analyze anything: is the 'Drift Cohort' "
                      "dataset still current? Check, don't guess.")
    ssh(f"rm -rf {drift_data}")
    cap2 = drive_turn(client, pid, tid,
                      "And now? Please check 'Drift Cohort' again.")
    caps = [cap1, cap2]
    txt1, txt2 = cap1["text"].lower(), cap2["text"].lower()
    return caps, [
        ("agent used check_import (not a fs walk)",
         len(tools_named(caps, "check_import")) >= 2),
        ("drift reported in plain language",
         any(w in txt1 for w in ("changed", "stale", "modified", "not current",
                                 "out of date"))),
        ("missing home reported",
         any(w in txt2 for w in ("gone", "missing", "unreachable", "no longer",
                                 "deleted", "removed"))),
        ("no weft jargon at the user",
         "dref:" not in txt1 + txt2 and "cas" not in txt1 + txt2.replace(
             "case", "").replace("cast", "")),
    ]


@scenario("produced_register")
def s_produced(client, pid, tid):
    cap1 = drive_turn(client, pid, tid,
                      "In python, write a small CSV named synth.csv with 100 "
                      "rows of two random columns (quick, no plotting), then "
                      "register it as dataset 'Synthetic 100'.")
    caps = [cap1]
    ent = dataset_by_title("Synthetic 100")
    if not ent:   # some agents ask to confirm — one nudge allowed
        caps.append(drive_turn(client, pid, tid,
                               "Yes — go ahead and register it."))
        ent = dataset_by_title("Synthetic 100")
    md = (ent or {}).get("metadata") or {}
    ap = (ent or {}).get("artifact_path")
    return caps, [
        ("run_python produced the file", bool(tools_named(caps, "run_python"))),
        ("dataset entity exists", ent is not None),
        ("content identity minted (CAS adopt)",
         str(md.get("ref", "")).startswith("dref:")),
        ("browsable copy exists", bool(ap) and os.path.exists(ap)),
    ]




@scenario("produce_keep_reuse")
def s_keep_reuse(client, pid, tid):
    """Produce → register → REUSE from a fresh thread (computation reuse,
    local): the second thread must build on the registered dataset, not
    regenerate it."""
    cap1 = drive_turn(client, pid, tid,
                      "In python, compute the first 200 prime numbers, save "
                      "them one per line to primes.txt, and register that "
                      "file as dataset 'Primes'. Quick utility work — no "
                      "plan needed.")
    caps = [cap1]
    ent = dataset_by_title("Primes")
    if not ent:
        caps.append(drive_turn(client, pid, tid, "Yes, go ahead."))
        ent = dataset_by_title("Primes")
    # fresh thread, same project — the reuse question
    tid2 = client.post("/api/threads",
                       json={"project_id": pid, "title": "reuse"}).json()["id"]
    cap2 = drive_turn(client, pid, tid2,
                      "Using the Primes dataset we already have in this "
                      "project, compute the sum of the primes in python and "
                      "tell me the value. Do not regenerate the primes.")
    caps.append(cap2)
    ap = (ent or {}).get("artifact_path") or ""
    base = os.path.basename(ap)
    reuse_runs = tools_named([cap2], "run_python")
    used_dataset = any(base and base in (t["input"].get("code") or "")
                       or ap and ap in (t["input"].get("code") or "")
                       for t in reuse_runs)
    regenerated = any("is_prime" in (t["input"].get("code") or "")
                      or "sympy" in (t["input"].get("code") or "")
                      for t in reuse_runs)
    return caps, [
        ("dataset registered in thread 1", ent is not None),
        ("thread 2 read the registered file", used_dataset),
        ("thread 2 did not regenerate primes", not regenerated),
        ("correct sum reported", "111587" in cap2["text"].replace(",", "")),
    ]


@scenario("keep_triage_and_whereabouts")
def s_keep_triage(client, pid, tid):
    """The keep conversation: explicit triage (keep the summary, drop the
    big intermediate), then a grounded 'where is it / is it safe' answer."""
    cap1 = drive_turn(client, pid, tid,
                      "Run a quick python step that writes two files: "
                      "summary.txt (a line of text) and big_intermediate.bin "
                      "(1 MB of zeros). Keep the summary for the project, "
                      "but the intermediate is scratch — make sure it is NOT "
                      "kept. Quick utility work, no plan needed.")
    caps = [cap1]
    triage = tools_named(caps, "keep_outputs")
    dropped = any("big_intermediate" in json.dumps(t["input"])
                  for t in triage)
    cap2 = drive_turn(client, pid, tid,
                      "Where exactly does summary.txt live now, and is it "
                      "safe / will it survive cleanup?")
    caps.append(cap2)
    txt2 = cap2["text"]
    grounded = "/" in txt2 and "summary.txt" in txt2
    safe_lang = any(w in txt2.lower() for w in
                    ("kept", "safe", "retained", "survive", "protected"))
    jargon = "dref:" in txt2 or " cas " in txt2.lower()
    return caps, [
        ("keep triage used (big intermediate dropped)",
         bool(triage) and dropped),
        ("whereabouts answered with a real path", grounded),
        ("safety stated in plain language", safe_lang and not jargon),
    ]


@scenario("retention_alert_loop")
def s_alert_loop(client, pid, tid):
    """A run whose keepers could not be kept (no durable storage) carries a
    retention_alert — the agent must read it and explain the levers, not
    guess or go silent."""
    from core.graph.entities import create_entity, get_entity, update_entity
    from core.graph.derivation import imported
    rid = create_entity(
        entity_type="run", title="Remote sweep (test)",
        derivation=imported("test"), metadata={
            "retention_alert": (
                "results not kept: the machine that ran this has no safe "
                "storage and the keepers total 5.2 GB. Options: declare "
                "durable storage on its machine card (Settings → Compute), "
                "or ask to ship them here explicitly.")})
    cap = drive_turn(client, pid, tid,
                     f"Why weren't the results of the run 'Remote sweep "
                     f"(test)' ({rid}) kept? What are my options?")
    caps = [cap]
    txt = cap["text"].lower()
    return caps, [
        ("alert was read (run consulted)",
         any(t["name"] in ("read_entity", "list_entities", "get_lineage",
                           "get_job_status")
             for t in cap["tools"]) or "5.2" in cap["text"]),
        ("explains the cause (no safe storage)",
         "storage" in txt and ("safe" in txt or "durable" in txt)),
        ("offers both levers",
         ("settings" in txt or "compute" in txt or "machine" in txt)
         and ("ship" in txt or "copy" in txt or "transfer" in txt
              or "bring" in txt)),
    ]


@scenario("bad_input_garbage_csv")
def s_garbage(client, pid, tid):
    """BAD-INPUTS journey (release_test_plan): a realistically messy file —
    numeric column polluted with 'n/a', '?', blanks, a stray ragged row —
    registered and analyzed. The agent must (i) report the TRUE computable
    sum over the valid entries, (ii) say HOW MANY entries it had to skip,
    and (iii) never present the file as clean. Fabricating a tidy answer
    over garbage is the failure this guards against."""
    import random
    rnd = random.Random(7)
    rows, bad = [], 0
    true_sum = 0
    for i in range(240):
        if i % 12 == 5:            # 20 polluted entries
            rows.append(f"{i},{rnd.choice(['n/a', '?', ''])}")
            bad += 1
        else:
            v = (i * 5) % 23
            true_sum += v
            rows.append(f"{i},{v}")
    rows.insert(100, "9999,3,EXTRA,COLUMNS,HERE")   # one ragged row
    (www / "messy.csv").write_text("id,val\n" + "\n".join(rows) + "\n")
    url = URL.rsplit("/", 1)[0] + "/messy.csv"
    cap = drive_turn(client, pid, tid,
        f"Register {url} as a dataset called 'Messy readings', then compute "
        f"the sum of the val column. Tell me the sum AND exactly how many "
        f"entries you had to skip or clean, and why.")
    caps = [cap]
    txt = cap["text"]
    # the ragged row's val=3 may legitimately be included (parsers differ);
    # accept either the strict sum or strict+3, but ONLY those two numbers
    ok_sum = str(true_sum) in txt or str(true_sum + 3) in txt
    mentions_skips = any(w in txt.lower() for w in
                         ("skip", "invalid", "non-numeric", "missing",
                          "malformed", "clean", "drop", "exclud"))
    # count honesty: 20 polluted (+1 ragged tolerated) — the number must sit
    # NEAR a skip/clean word, not merely anywhere in the reply
    import re
    ok_count = bool(re.search(
        r"(?:skip\w*|drop\w*|exclud\w*|invalid|non.numeric|missing|malformed|"
        r"clean\w*)\D{0,60}\b2[01]\b"
        r"|\b2[01]\b\D{0,60}(?:skip\w*|drop\w*|exclud\w*|invalid|non.numeric|"
        r"missing|malformed|entries|rows|values)", txt, re.I))
    return caps, [
        ("dataset registered", any(t["name"] == "register_dataset"
                                   for t in cap["tools"])),
        ("reported sum is the TRUE computable sum (no fabrication)", ok_sum),
        ("agent discloses the messy entries", mentions_skips),
        ("skip-count is the true count (20, +1 if ragged row dropped)",
         ok_count),
    ]


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    # a real remote site in the throwaway workspace
    from core.compute import adapter as ad
    st = ad.configure()
    assert st["ok"], st["detail"]
    r = ad.get_compute().sync_call(
        "register_site", "mendel", "ssh",
        {"root": "/home/pkharchenko/aba-dstest-weft2", "host": "mendel"})
    assert r.get("site") == "mendel", r
    print("[study] mendel registered in throwaway workspace")

    scenarios = [(fn._scenario, fn) for fn in
                 [s_url, s_reuse, s_remote, s_drift, s_produced,
                  s_keep_reuse, s_keep_triage, s_alert_loop, s_garbage]]
    if only:
        known = {name for name, _ in scenarios}
        unknown = only - known
        if unknown:
            sys.exit(f"[study] unknown scenario(s): {', '.join(sorted(unknown))}"
                     f" — known: {', '.join(sorted(known))}")
    try:
        with TestClient(app) as client:
            try:
                for name, fn in scenarios:
                    if only and name not in only:
                        continue
                    run_scenario(client, name, fn)
            finally:
                # BEFORE TestClient exit — app shutdown takes the adapter down
                try:
                    ad.get_compute().sync_call("site_unregister", "mendel")
                    print("[cleanup] mendel site unregistered")
                except Exception as e:  # noqa: BLE001
                    print("[cleanup] unregister:", e)
    finally:
        out = ssh("rm -rf /home/pkharchenko/aba-dstest-weft2 "
                  "/home/pkharchenko/aba-dstest-data2 && echo cleaned")
        print("[cleanup] mendel dirs:", out.stdout.strip() or out.stderr[-120:])
        srv.shutdown()
    if not RESULTS:
        sys.exit("[study] zero scenarios ran — refusing a vacuous ALL PASS")
    print("\nSTUDY:", "ALL PASS" if all(ok for _, ok in RESULTS)
          else "FAILURES: " + ", ".join(n for n, ok in RESULTS if not ok))
    sys.exit(0 if all(ok for _, ok in RESULTS) else 1)


if __name__ == "__main__":
    main()
