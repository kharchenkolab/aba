"""Live-drive v2: exercises EVERYTHING the toy v1 missed.

Scenario (~3-5 min):
  1. "list any data files in this project"           — exercise list_data_files
  2. "now read a file at /nonexistent/path/x.csv"    — force file-open error → P2 preamble re-fire
  3. "search the skills index for plotting recipes"  — exercise search_skills
  4. "use describe_tool to show me the full schema for view_artifact" — verify L3 escape hatch
  5. "make a quick plan: produce a 2x2 panel of random points"
                                                     — present_plan opens new Run → P3 preamble
  6. "Go ahead with the plan as proposed."           — run_python in new Run
  7. "save a memory note about today's testing"      — write_memory (was previously curated-out)
  8. "what tools are available to update an existing figure?"
                                                     — describe_tool follow-up

Between every turn we snapshot:
  • tool calls + counts
  • prompt size trend (input + cache_read + cache_write)
  • thread_summaries (Tier-2 fired?)
  • path-preamble events found in tool_result stdout
  • the FULL llm_history via /api/dev/last-turn-context (for user spot-checks)

At the end we dump the last turn's full context to a file the user can
read, plus a friction-analysis summary.
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = os.environ.get("ABA_BASE", "http://127.0.0.1:8000")
HOME = Path.home()


def _http(method: str, path: str, body=None, headers=None) -> tuple[int, str]:
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
    body = {"text": text, "thread_id": tid, "project_id": pid}
    req = urllib.request.Request(
        BASE + "/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "X-Project-Id": pid, "Accept": "text/event-stream"},
        method="POST")
    s = {"tools": [], "stop_reason": None, "ok": False,
         "elapsed_s": 0.0, "events": 0}
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
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
                    s["events"] += 1
                    t = ev.get("type") or ""
                    if t == "tool_start":
                        n = ev.get("name") or "?"
                        s["tools"].append(n)
                        print(f"    🔧 {n}", flush=True)
                    elif t in ("done", "turn_done"):
                        s["stop_reason"] = ev.get("stop_reason") or "done"
                        s["ok"] = True
                        break
                    elif t in ("halt", "turn_halt", "error"):
                        s["stop_reason"] = ev.get("reason") or t
                        print(f"    ⚠ halt {s['stop_reason']}", flush=True)
                        break
                if s["ok"] or s["stop_reason"]:
                    break
    except urllib.error.HTTPError as e:
        s["stop_reason"] = f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        s["stop_reason"] = f"{type(e).__name__}: {e}"
    s["elapsed_s"] = round(time.time() - t0, 1)
    return s


def _snap(pid: str, tid: str) -> dict:
    db = HOME / ".aba" / "runtime" / "projects" / pid / "project.db"
    snap: dict = {}
    if not db.is_file():
        return {"error": "no db"}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    r = con.execute(
        "SELECT agent_spec_name, state, "
        "json_extract(usage_blob,'$.input'),"
        "json_extract(usage_blob,'$.cache_read'),"
        "json_extract(usage_blob,'$.cache_write'),"
        "json_extract(usage_blob,'$.output') "
        "FROM runs WHERE thread_id=? "
        "ORDER BY updated_at DESC LIMIT 1", (tid,)).fetchone()
    if r:
        snap["run"] = {"spec": r[0], "state": r[1],
                       "in": r[2], "cr": r[3], "cw": r[4], "out": r[5],
                       "total_prompt": (r[2] or 0)+(r[3] or 0)+(r[4] or 0)}
    m = con.execute("SELECT COUNT(*), COALESCE(SUM(length(content)),0) "
                    "FROM messages WHERE thread_id=?", (tid,)).fetchone()
    snap["msgs"], snap["m_chars"] = m[0], m[1]
    try:
        srow = con.execute(
            "SELECT covered_until, length(summary) FROM thread_summaries "
            "WHERE thread_id=?", (tid,)).fetchone()
        snap["tier2"] = ({"covered": srow[0], "len": srow[1]}
                         if srow else None)
    except sqlite3.OperationalError:
        snap["tier2"] = None
    con.close()

    # Path preambles found in any tool_result so far
    jsonl = HOME / ".aba" / "runtime" / "projects" / pid / "threads" / f"{tid}.jsonl"
    pre_count = 0
    if jsonl.is_file():
        for line in jsonl.read_text().splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            for c in e.get("content") or []:
                if c.get("type") == "tool_result":
                    txt = str(c.get("content", ""))
                    if ("Workspace orientation" in txt
                        or "Fresh kernel" in txt
                        or "cwd just shifted" in txt):
                        pre_count += 1
    snap["preambles"] = pre_count
    return snap


def _print_snap(s: dict, label: str) -> None:
    r = s.get("run") or {}
    t2 = s.get("tier2")
    t2s = f"Tier-2 covered={t2['covered']} {t2['len']}c" if t2 else "Tier-2 not fired"
    print(f"  📸 {label}: msgs={s.get('msgs')} hist={s.get('m_chars'):,}c "
          f"| state={r.get('state')} in={r.get('in')} cr={r.get('cr')} "
          f"cw={r.get('cw')} out={r.get('out')} total={r.get('total_prompt')} "
          f"| preambles={s.get('preambles')} | {t2s}", flush=True)


SCENARIO = [
    "list any data files in this project",
    "now try to read a file at /nonexistent/path/x.csv from R — show me the exact error",
    "search the skills index for recipes about plotting",
    "use describe_tool to show me the full schema for view_artifact",
    "make a quick plan: produce a 2x2 panel of random scatter points in python",
    "Go ahead with the plan as proposed.",
    "save a memory note titled 'live-drive-v2' with the body 'today I tested the lean redesign — compression + describe_tool + path-refire'",
    "what tools are available to update an existing figure in the latest result?",
]


def main() -> int:
    code, body = _http("GET", "/api/projects/current")
    cur = json.loads(body) if code == 200 else {}
    pid = (cur or {}).get("id") or (cur or {}).get("current")
    if not pid:
        print("ERROR: no current project. Open one in the UI first.")
        return 2
    print(f"project: {pid}")

    code, body = _http("POST", "/api/threads",
                       body={"title": "live-drive v2 — lean redesign",
                             "question": "exercise compression + describe + path-refire + Tier-2"},
                       headers={"X-Project-Id": pid})
    if code != 200:
        print(f"thread create failed: {code} {body[:200]}"); return 2
    tid = json.loads(body)["id"]
    print(f"thread:  {tid}\n")
    _print_snap(_snap(pid, tid), "initial")

    drive_t0 = time.time()
    for i, msg in enumerate(SCENARIO, 1):
        print(f"\n— TURN {i}/{len(SCENARIO)} — {msg!r}", flush=True)
        s = _stream_chat(pid, tid, msg)
        tools = ", ".join(s["tools"]) or "(none)"
        print(f"    done in {s['elapsed_s']}s stop={s['stop_reason']} "
              f"events={s['events']} tools=[{tools}]", flush=True)
        _print_snap(_snap(pid, tid), f"after turn {i}")
    print(f"\n=== drive done in {time.time()-drive_t0:.1f}s ===\n")

    # ── Spot-check the FULL llm_history for the latest turn ─────────
    code, body = _http("GET", "/api/dev/last-turn-context",
                       headers={"X-Project-Id": pid})
    if code == 200:
        ctx = json.loads(body)
        hist = ctx.get("history") or []
        sys_t = ctx.get("system") or ""
        tools = ctx.get("tools") or []
        # Save the full context to a file the user can read.
        out = HOME / "live_drive_v2_lastctx.json"
        out.write_text(json.dumps(ctx, indent=2, default=str)[:1_500_000])
        print(f"\nFULL CONTEXT dumped to {out}")
        print(f"  system : {len(sys_t):,} chars (~{len(sys_t)//4:,} tokens)")
        print(f"  history: {len(hist)} msgs, {len(json.dumps(hist)):,} chars")
        print(f"  tools  : {len(tools)} (catalog from latest call)")
        # Check Tier-2 substitution: msg[0] should be the summary if Tier-2 fired
        head = json.dumps(hist[0]) if hist else ""
        if "<summary>" in head or "SYSTEM SUMMARY" in head:
            print("  ✓ msg[0] is the Tier-2 summary")
        else:
            print(f"  msg[0] head: {head[:140]}")
        # Spot-check: which compacted tools are in the catalog, how many describe-full?
        # (When run live, ctx['tools'] is just names — but we can verify via API.)
    else:
        print(f"\ndev endpoint returned {code}: {body[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
