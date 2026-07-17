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
# against the real store). installation: the recipe/skill bundle, read-only.
for name in ("oauth.json", "installation"):
    src = INSTALL / name
    if src.exists():
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
            t = ev.get("type")
            if t == "delta":
                cap["text"].append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                cap["tools"].append({"name": ev.get("name") or ev.get("tool"),
                                     "input": ev.get("input") or {}})
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


def run_scenario(client, name, fn):
    pid = client.post("/api/projects", json={"name": f"ds-{name}"}).json()["id"]
    client.post(f"/api/projects/{pid}/open")
    tid = client.post("/api/threads",
                      json={"project_id": pid, "title": name}).json()["id"]
    t0 = time.time()
    try:
        caps, checks = fn(client, pid, tid)
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
    cap = drive_turn(client, pid, tid,
                     f"Register {URL} as a dataset named 'Portal Table Again'.")
    caps = [cap]
    n = len([r for r in find_entities(type="dataset", not_deleted=True)
             if ((get_entity(r["id"]) or {}).get("metadata") or {})
             .get("source_key") == URL])
    txt = all_text(caps).lower()
    return caps, [
        ("only ONE dataset for this url exists", n == 1),
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
    ssh(f"mkdir -p {R_DATA} && head -c 1000 /dev/urandom > {R_DATA}/a.bin")
    # register via the tool directly (fixture step, not under study)
    from content.bio.tools.curation import register_dataset_tool
    r = register_dataset_tool({"title": "Drift Cohort", "path": R_DATA,
                               "site": "mendel"}, {})
    assert r.get("status") == "ok", r
    ssh(f"echo extra >> {R_DATA}/a.bin")
    cap1 = drive_turn(client, pid, tid,
                      "Before we analyze anything: is the 'Drift Cohort' "
                      "dataset still current? Check, don't guess.")
    ssh(f"rm -rf {os.path.dirname(R_DATA)}")
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
                 [s_url, s_reuse, s_remote, s_drift, s_produced]]
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
    print("\nSTUDY:", "ALL PASS" if all(ok for _, ok in RESULTS)
          else "FAILURES: " + ", ".join(n for n, ok in RESULTS if not ok))
    sys.exit(0 if all(ok for _, ok in RESULTS) else 1)


if __name__ == "__main__":
    main()
