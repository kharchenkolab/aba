"""Live-drive the chat endpoint with a SHORT lean-tool scenario,
streaming each event as it arrives so progress is visible.

The scenario hits 5 turns that exercise the lean-spec discovery path
(`list_data_files → search_skills → Skill → write_memory → read_memory`)
without any scanpy execution — so the whole drive finishes in ~1–2
minutes, not the ~8–12 of the original GEO-scrape scenario.

After the drive we run `maybe_summarize` directly on the resulting
history with a tiny budget to prove Tier-2 actually fires + writes a
row to `thread_summaries`. That covers the silent-failure mode that
hid in prj_30d7535f without forcing a scanpy run to bloat history.

Run:
    ABA_BASE=http://127.0.0.1:8000 ~/.aba/env/bin/python3 \\
        /Users/peter.kharchenko/aba/aba/scripts/live_drive_lean.py
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = os.environ.get("ABA_BASE", "http://127.0.0.1:8000")
HOME = Path.home()


def _http_json(method: str, path: str, body: dict | None = None,
               headers: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _stream_chat(pid: str, tid: str, text: str) -> dict:
    """POST /api/chat and consume the SSE stream, printing events
    live. Returns a structured summary of what happened in this turn."""
    body = {"text": text, "thread_id": tid, "project_id": pid}
    req = urllib.request.Request(
        BASE + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "X-Project-Id": pid, "Accept": "text/event-stream"},
        method="POST",
    )
    summary = {"tools": [], "stop_reason": None, "ok": False,
               "elapsed_s": 0.0, "events": 0, "text_chunks": 0}
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            # SSE = lines `event: <name>\ndata: <json>\n\n`. We don't
            # need the event-name framing; reading data: lines is enough.
            buf = b""
            for chunk in iter(lambda: r.read(4096), b""):
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode(errors="replace").rstrip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        ev = json.loads(payload)
                    except Exception:
                        continue
                    summary["events"] += 1
                    # Real-server event payloads (from backend/guide.py):
                    #   {"type":"tool_start","name":...,"input":...}
                    #   {"type":"tool_done","tool_name":...}
                    #   {"type":"text","text":...} (incremental deltas)
                    #   {"type":"done"} / {"type":"halt", reason:...}
                    t = ev.get("type") or ev.get("kind") or ""
                    if t == "tool_start":
                        n = ev.get("name") or ev.get("tool_name") or "?"
                        summary["tools"].append(n)
                        print(f"    🔧 {n}", flush=True)
                    elif t == "text":
                        summary["text_chunks"] += 1
                    elif t in ("done", "turn_done"):
                        summary["stop_reason"] = ev.get("stop_reason") or "done"
                        summary["ok"] = True
                        break
                    elif t in ("halt", "turn_halt", "error"):
                        summary["stop_reason"] = ev.get("reason") or t
                        print(f"    ⚠  halt: {summary['stop_reason']}", flush=True)
                        break
                if summary["ok"] or summary["stop_reason"]:
                    break
    except urllib.error.HTTPError as e:
        summary["stop_reason"] = f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        summary["stop_reason"] = f"{type(e).__name__}: {e}"
    summary["elapsed_s"] = round(time.time() - t0, 1)
    return summary


def _snapshot(pid: str, tid: str) -> dict:
    db = HOME / ".aba" / "runtime" / "projects" / pid / "project.db"
    snap: dict = {}
    if not db.is_file():
        return {"error": "no project.db"}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = con.execute(
        "SELECT run_id, agent_spec_name, state, "
        "json_extract(usage_blob,'$.input'),"
        "json_extract(usage_blob,'$.cache_read'),"
        "json_extract(usage_blob,'$.cache_write'),"
        "json_extract(usage_blob,'$.output') "
        "FROM runs WHERE thread_id=? ORDER BY updated_at DESC LIMIT 1",
        (tid,)).fetchone()
    if row:
        snap["run"] = {
            "spec":  row[1], "state": row[2],
            "in":    row[3], "cr":    row[4],
            "cw":    row[5], "out":   row[6],
            "total_prompt": (row[3] or 0) + (row[4] or 0) + (row[5] or 0),
        }
    m = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(content)),0) "
        "FROM messages WHERE thread_id=?", (tid,)).fetchone()
    snap["msgs"]    = m[0]
    snap["m_chars"] = m[1]
    try:
        srow = con.execute(
            "SELECT covered_until, length(summary) FROM thread_summaries "
            "WHERE thread_id=?", (tid,)).fetchone()
        snap["tier2"] = {"covered": srow[0], "len": srow[1]} if srow else None
    except sqlite3.OperationalError:
        snap["tier2"] = None
    con.close()
    return snap


def _print_snap(s: dict, label: str) -> None:
    if "error" in s:
        print(f"  📸 {label}: {s['error']}"); return
    r = s.get("run") or {}
    t2 = s.get("tier2")
    t2s = f"Tier-2: covered={t2['covered']}, {t2['len']}c" if t2 else "Tier-2: not fired"
    print(f"  📸 {label}: msgs={s['msgs']}, hist={s['m_chars']:,}c | "
          f"spec={r.get('spec')} state={r.get('state')} "
          f"in={r.get('in')} cr={r.get('cr')} cw={r.get('cw')} out={r.get('out')} "
          f"total_in={r.get('total_prompt')} | {t2s}", flush=True)


SCENARIO = [
    "list any data files in this project",
    "search the skills index for recipes about GEO datasets",
    "fetch the body of whichever skill from that list looks most relevant",
    "save a memory note titled 'lean-test' with the body 'today I'm testing the lean spec — search_skills + Skill is the discovery pattern.'",
    "what memory notes have I saved so far?",
]


def main() -> int:
    code, body = _http_json("GET", "/api/projects/current")
    cur = json.loads(body) if code == 200 else {}
    pid = (cur or {}).get("id") or (cur or {}).get("current")
    if not pid:
        print("ERROR: no current project. Open one in the UI first.")
        return 2
    print(f"project: {pid}")

    code, body = _http_json("POST", "/api/threads",
                            body={"title": "live-drive lean (short)",
                                  "question": "lean smoke"},
                            headers={"X-Project-Id": pid})
    if code != 200:
        print(f"thread create failed: {code} {body[:200]}"); return 2
    tid = json.loads(body)["id"]
    print(f"thread:  {tid}")
    _print_snap(_snapshot(pid, tid), "initial")

    drive_t0 = time.time()
    for i, msg in enumerate(SCENARIO, 1):
        print(f"\n— TURN {i}/5 — {msg!r}", flush=True)
        s = _stream_chat(pid, tid, msg)
        tools = ", ".join(s["tools"]) or "(none)"
        print(f"    done in {s['elapsed_s']}s, "
              f"stop={s['stop_reason']}, events={s['events']}, "
              f"tools=[{tools}]", flush=True)
        _print_snap(_snapshot(pid, tid), f"after turn {i}")
    drive_dt = time.time() - drive_t0
    print(f"\n=== drive done in {drive_dt:.1f}s ===\n")

    # ── Tier-2 post-check ─────────────────────────────────────────
    # Force the budget low enough that the resulting history triggers
    # synthesis. Proves the END-TO-END Tier-2 path works on real data
    # without needing to bloat history to 25k+ chars (which would
    # require many more turns / scanpy).
    print("Tier-2 post-check: force budget=2000 chars, run maybe_summarize")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    for ln in (HOME / ".aba" / "config.env").read_text().splitlines():
        ln = ln.strip()
        if ln.startswith("export "):
            kv = ln[7:].split("=", 1)
            if len(kv) == 2:
                os.environ.setdefault(kv[0], kv[1])
    db = HOME / ".aba" / "runtime" / "projects" / pid / "project.db"
    os.environ["ABA_DB_PATH"] = str(db)
    from core.graph._schema import set_db_path; set_db_path(str(db))
    import content.bio                                          # noqa: F401
    from core.summarize.budget_summary import maybe_summarize, tier2_diag

    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT role, content FROM messages "
                       "WHERE thread_id=? ORDER BY id", (tid,)).fetchall()
    con.close()
    msgs = [{"role": r[0], "content": json.loads(r[1])} for r in rows]
    print(f"  raw history: {len(msgs)} msgs")
    out = maybe_summarize(tid, msgs, budget_chars=2_000)
    print(f"  after maybe_summarize: {len(out)} msgs (was {len(msgs)})")
    print(f"  Tier-2 diag: {tier2_diag()}")
    if len(out) < len(msgs):
        head = json.dumps(out[0])
        print(f"  msg-0 head: {head[:200]}")
    _print_snap(_snapshot(pid, tid), "after Tier-2 post-check")

    return 0


if __name__ == "__main__":
    sys.exit(main())
