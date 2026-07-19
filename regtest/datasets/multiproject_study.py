"""Multi-PROJECT live study (release_test_plan: 'Concurrent projects' at the
REAL deployment shape). Every other live harness sets ABA_DB_PATH → SINGLE
mode — the vacuous-truth-sweep bug proved that blind spot bites. This one runs
MULTI-project mode (no ABA_DB_PATH): two projects with real per-project DBs,
interleaved turns, a background job in each, and NO cross-project bleed.

Run:  python regtest/datasets/multiproject_study.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL = Path(os.environ.get("ABA_INSTALL") or os.environ.get("ABA_HOME")
               or (Path.home() / ".aba"))


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
_tmp = Path(tempfile.mkdtemp(prefix="aba_mpstudy_"))
HOME = _tmp / "home"
HOME.mkdir(parents=True)


def _store_fresh() -> bool:
    try:
        d = json.load(open(INSTALL / "oauth.json"))
        return (d.get("expires_at") or 0) > time.time() + 120
    except Exception:  # noqa: BLE001
        return False


for name in ("oauth.json", "installation"):
    src = INSTALL / name
    if not src.exists():
        continue
    if name == "oauth.json" and not _store_fresh():
        r = subprocess.run(["security", "find-generic-password",
                            "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True)
        tok = ""
        if r.returncode == 0:
            try:
                tok = (json.loads(r.stdout).get("claudeAiOauth") or {}) \
                    .get("accessToken") or ""
            except Exception:  # noqa: BLE001
                pass
        if tok:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = tok
            print("[mp] oauth store stale — bridging via CLI credential")
            continue
        sys.exit("[mp] no usable credential — re-login aba, then rerun")
    os.symlink(src, HOME / name)
os.environ["ABA_HOME"] = str(HOME)
os.environ["ABA_RUNTIME_DIR"] = str(_tmp / "runtime")
os.environ["ABA_RAW_REQUEST_DIR"] = str(_tmp / "rawreq")
os.environ.pop("ABA_DB_PATH", None)          # MULTI-project mode — the point
print(f"[mp] throwaway home (multi-project): {HOME}")

sys.path.insert(0, str(REPO / "backend"))

import content.bio  # noqa: E402,F401
from core.graph._schema import init_db  # noqa: E402

init_db()
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402


def drive_turn(client, pid, tid, text, timeout_s=900):
    cap = {"prompt": text, "tools": [], "text": []}
    with client.stream("POST", "/api/chat",
                       json={"text": text, "project_id": pid,
                             "thread_id": tid}) as r:
        for line in r.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:  # noqa: BLE001
                continue
            if ev.get("type") == "delta":
                cap["text"].append(ev.get("text") or ev.get("delta") or "")
            elif ev.get("type") == "tool_start":
                cap["tools"].append({"name": ev.get("name") or ev.get("tool"),
                                     "input": ev.get("input") or {}})
    cap["text"] = "".join(cap["text"]).strip()
    return cap


def wait_quiet(client, pid, timeout_s=240):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = client.get(f"/api/jobs?project_id={pid}")
        rows = r.json() if r.status_code == 200 else []
        rows = rows if isinstance(rows, list) else rows.get("jobs", [])
        if not any(j.get("status") in ("queued", "running") for j in rows):
            return rows
        time.sleep(5)
    return []


def main() -> None:
    checks: list = []
    with TestClient(app) as client:
        pa = client.post("/api/projects", json={"name": "mp-alpha"}).json()["id"]
        pb = client.post("/api/projects", json={"name": "mp-beta"}).json()["id"]
        checks.append(("two REAL projects created (multi mode)",
                       pa != pb and not str(pa).startswith("single")))
        client.post(f"/api/projects/{pa}/open")
        ta = client.post("/api/threads",
                         json={"project_id": pa, "title": "a"}).json()["id"]
        client.post(f"/api/projects/{pb}/open")
        tb = client.post("/api/threads",
                         json={"project_id": pb, "title": "b"}).json()["id"]

        ea = sum((i * 3) % 11 for i in range(500))
        eb = sum((i * 7) % 19 for i in range(500))
        # interleaved: A computes, B computes, A background, B asks, A asks
        drive_turn(client, pa, ta,
                   "Compute the sum of (i*3) mod 11 for i in 0..499 and "
                   "remember it as this project's ALPHA total. Tell me it.")
        drive_turn(client, pb, tb,
                   "Compute the sum of (i*7) mod 19 for i in 0..499 and "
                   "remember it as this project's BETA total. Tell me it.")
        drive_turn(client, pa, ta,
                   "Run a BACKGROUND local job: sleep 20 seconds, then print "
                   "ALPHA-DONE and the ALPHA total again. Don't wait for it.")
        rows_a = wait_quiet(client, pa)
        checks.append(("project-A background job landed DONE in ITS OWN DB",
                       any(j.get("status") == "done" for j in rows_a)))

        qa = drive_turn(client, pa, ta,
                        "What is this project's total, and does this project "
                        "know anything about a BETA total? Answer honestly.")
        qb = drive_turn(client, pb, tb,
                        "What is this project's total, and does this project "
                        "know anything about an ALPHA total? Answer honestly.")
        checks.append(("A knows ALPHA total", str(ea) in qa["text"].replace(",", "")))
        checks.append(("B knows BETA total", str(eb) in qb["text"].replace(",", "")))
        checks.append(("A does NOT report B's number",
                       str(eb) not in qa["text"].replace(",", "")))
        checks.append(("B does NOT report A's number",
                       str(ea) not in qb["text"].replace(",", "")))

        # structural truth: per-project DB files really exist (multi mode)
        from core.config import PROJECTS_DIR
        dbs = [p / "project.db" for p in PROJECTS_DIR.iterdir()
               if p.is_dir() and (p / "project.db").exists()] \
            if PROJECTS_DIR.exists() else []
        checks.append(("per-project DB files exist (>=2)", len(dbs) >= 2))

        # truth sweep over the per-project DBs (the multi branch, meaningful
        # here): no done+error rows, no substrate-DONE-but-failed rows
        import sqlite3
        from core.compute import adapter as ad
        bad = []
        try:
            comp = ad.get_compute()
        except Exception:  # noqa: BLE001
            comp = None
        for db in dbs:
            c = sqlite3.connect(db); c.row_factory = sqlite3.Row
            try:
                if not c.execute("SELECT 1 FROM sqlite_master WHERE "
                                 "name='jobs'").fetchone():
                    continue
                for r in c.execute(
                        "SELECT id,status,error,params FROM jobs").fetchall():
                    if r["status"] == "done" and (r["error"] or "").strip():
                        bad.append(f"{r['id']}: done+error")
                    p = json.loads(r["params"] or "{}")
                    wid = p.get("weft_id")
                    if comp and wid and r["status"] == "failed":
                        try:
                            st = comp.sync_call("task_status", wid)
                            if st and st[0]["state"] == "DONE":
                                bad.append(f"{r['id']}: substrate DONE but failed")
                        except Exception:  # noqa: BLE001
                            pass
            finally:
                c.close()
        checks.append(("jobs-vs-substrate truth sweep clean (multi-project)",
                       not bad))
        checks += [(f"truth: {b}", False) for b in bad]

    print("\n== multiproject_separation ==")
    for label, ok in checks:
        print(f"    {'✓' if ok else '✗'} {label}")
    ok = all(v for _, v in checks)
    print("MULTIPROJECT STUDY:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
