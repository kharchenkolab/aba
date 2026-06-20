"""Shared infrastructure for live-driven chat scenarios.

Each scenario seeds a fresh project + thread on the live aba server
and POSTs the user_prompt to /api/chat, then consumes the SSE stream
to record what tools the model called. Evaluates the same Assertion
predicates from tests.scenarios.

By construction this exercises the exact code path a live UI session
takes — same prompt assembly, same tool dispatch, same MCP gateway —
so a scenario passing here means a real session will too. Slower
than the offline driver (~30s per scenario depending on the recipe),
but truthful.

The runner does NOT mock tools. Scenarios with side-effecting tools
(run_python that downloads GEO data, register_dataset that creates
rows, …) bound their work via `max_turns` so the test stops at a
known event boundary before things get expensive.

A test that needs assertions about the assistant's final text or
about Tier-2 / preamble firing can post-process via the project DB:
each runner result includes the project_id + thread_id so a follow-
up sql query can inspect runs.usage_blob / thread_summaries / etc.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.scenarios import Scenario


BASE = os.environ.get("ABA_BASE", "http://127.0.0.1:8000")
HOME = Path.home()


@dataclass
class LiveRunReport:
    scenario:     str
    project_id:   str
    thread_id:    str
    runtime:      str          # whatever the server's currently using
    calls:        list[tuple[str, dict]]   # (name, input)
    last_text:    str
    halted:       bool
    halt_reason:  str
    elapsed_s:    float
    raw_events:   list[dict]   # full SSE stream for debugging


def server_reachable() -> bool:
    try:
        urllib.request.urlopen(BASE + "/api/specs/primary", timeout=2).read()
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _http(method: str, path: str, body=None, headers=None,
          timeout: int = 60) -> tuple[int, str]:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _fresh_project(label: str) -> str:
    code, body = _http("POST", "/api/projects",
                       body={"title": f"scenario:{label}"})
    if code != 200:
        raise RuntimeError(f"project create failed: {code} {body[:200]}")
    pid = json.loads(body)["id"]
    _http("POST", f"/api/projects/{pid}/open")
    return pid


def _fresh_thread(pid: str, label: str) -> str:
    code, body = _http("POST", "/api/threads",
                       body={"title": f"scenario:{label}",
                             "question": ""},
                       headers={"X-Project-Id": pid})
    if code != 200:
        raise RuntimeError(f"thread create failed: {code} {body[:200]}")
    return json.loads(body)["id"]


def _consume_chat_sse(pid: str, tid: str, prompt: str,
                      max_turns: int,
                      stop_after_n_tools: int | None = None,
                      timeout_s: int = 300) -> tuple[
        list[tuple[str, dict]], str, bool, str, list[dict]]:
    """POST /api/chat and read the SSE stream until:
      - the server signals 'done' or 'halt', OR
      - `stop_after_n_tools` tool dispatches have been recorded
        (we close the response, which signals client-disconnect
        upstream and cancels any in-flight tool execution), OR
      - the pessimistic max_turns * 4 ceiling kicks in.

    Returns:
      calls:       chronological [(tool_name, tool_input)]
      last_text:   final assistant text concatenation
      halted:      True iff a TurnHalt-type event fired
      halt_reason: the halt's reason if any
      raw_events:  every parsed event for debugging
    """
    pessimistic_ceiling = (stop_after_n_tools
                           if stop_after_n_tools is not None
                           else max_turns * 4)
    req = urllib.request.Request(
        BASE + "/api/chat",
        data=json.dumps({"text": prompt,
                          "thread_id": tid,
                          "project_id": pid}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Project-Id": pid,
                 "Accept":       "text/event-stream"},
        method="POST",
    )
    calls:       list[tuple[str, dict]] = []
    raw_events:  list[dict]           = []
    text_buf:    list[str]            = []
    halted   = False
    halt_reason = ""
    tool_dispatches = 0

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            buf = b""
            for chunk in iter(lambda: r.read(8192), b""):
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
                    except Exception:                          # noqa: BLE001
                        continue
                    raw_events.append(ev)
                    t = ev.get("type") or ev.get("kind") or ""
                    if t == "tool_start":
                        name  = ev.get("name") or ev.get("tool_name") or "?"
                        input_ = ev.get("input") or {}
                        if not isinstance(input_, dict):
                            input_ = {"_raw": input_}
                        calls.append((name, input_))
                        tool_dispatches += 1
                        if tool_dispatches >= pessimistic_ceiling:
                            # Done observing — close the underlying
                            # connection to signal client-disconnect
                            # so the server cancels any in-flight
                            # tool execution (run_python jobs, etc.).
                            try:
                                r.close()
                            except Exception:                  # noqa: BLE001
                                pass
                            return (calls, "".join(text_buf),
                                    halted, halt_reason, raw_events)
                    elif t == "text" or t == "delta":
                        txt = ev.get("text") or ""
                        if txt:
                            text_buf.append(txt)
                    elif t in ("done", "turn_done"):
                        # Server signals end of THIS chat call;
                        # we don't follow up with another user msg
                        # so the loop ends here.
                        return (calls, "".join(text_buf),
                                halted, halt_reason, raw_events)
                    elif t in ("halt", "turn_halt", "error"):
                        halted = True
                        halt_reason = (ev.get("reason")
                                       or ev.get("message") or t)
                        return (calls, "".join(text_buf),
                                halted, halt_reason, raw_events)
    except urllib.error.HTTPError as e:
        halted = True
        halt_reason = f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:                                     # noqa: BLE001
        halted = True
        halt_reason = f"{type(e).__name__}: {e}"

    return calls, "".join(text_buf), halted, halt_reason, raw_events


def run_scenario_live(s: Scenario) -> LiveRunReport:
    """Drive `s` end-to-end through the live server. Caller evaluates
    `s.assertions` on the returned `calls` list."""
    t0 = time.time()
    pid = _fresh_project(s.name)
    tid = _fresh_thread(pid, s.name)

    # Pull the active spec/model so the report can record what
    # actually served the turn.
    runtime = "?"
    code, body = _http("GET", "/api/specs/primary")
    if code == 200:
        d = json.loads(body)
        active = d.get("default")
        for ent in (d.get("specs") or []):
            if ent.get("name") == active:
                runtime = f"{ent.get('model','?')} (mode={ent.get('prompt_mode','?')})"
                break

    calls, last_text, halted, halt_reason, raw = _consume_chat_sse(
        pid, tid, s.user_prompt,
        max_turns=s.max_turns,
        stop_after_n_tools=s.stop_after_n_tools)

    return LiveRunReport(
        scenario     = s.name,
        project_id   = pid,
        thread_id    = tid,
        runtime      = runtime,
        calls        = calls,
        last_text    = last_text[:600],
        halted       = halted,
        halt_reason  = halt_reason,
        elapsed_s    = round(time.time() - t0, 1),
        raw_events   = raw,
    )
