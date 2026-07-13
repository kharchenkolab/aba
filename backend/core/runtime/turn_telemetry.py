"""Per-Turn telemetry sinks (WU-5 extraction).

Two debug-aid helpers extracted from guide.py:

  - `dump_turn_context` — once per turn, persist the EXACT context the
    agent receives (system prompt + history + offered tools) to
    `$ABA_TURN_LOG_DIR` (default /tmp/aba_turnlog) for offline
    inspection. Writes both a Markdown file (human-readable) and a
    JSON sidecar (Context-tab consumer).
  - `live_log_event` — append one compact line per SSE event to the
    rolling live transcript. Tail `live.log` to watch any run in
    progress (including browser-driven sessions).

Both are best-effort: never raise, just swallow on I/O failure. Set
`ABA_TURN_LOG_DIR=off` (or empty / `0` / `false`) to disable.
"""
from __future__ import annotations

from core import config


def _log_dir() -> str:
    """Resolve the turnlog directory. Returns empty string when
    logging is disabled."""
    d = config.settings.turn_log_dir.get()
    if d.strip().lower() in ("", "off", "0", "false"):
        return ""
    return d


def dump_turn_context(run_id, *, user_text, system, history, active_tools,
                      model, thread_id, focus_entity_id) -> None:
    """Best-effort: persist the EXACT context the agent receives this turn (the
    assembled system prompt + the message history + the offered tools) to a file,
    so a human-driven run can be inspected afterward ("what did the agent actually
    see?"). One file per turn under ABA_TURN_LOG_DIR (default /tmp/aba_turnlog);
    set that env to '' / 'off' to disable. Never raises — debug aid only."""
    d = _log_dir()
    if not d:
        return
    try:
        import os, json as _json, datetime as _dt
        os.makedirs(d, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        tools = [t.get("name") for t in (active_tools or [])]
        body = "\n".join([
            f"# Turn {run_id}  ({ts})",
            f"thread={thread_id}  focus={focus_entity_id}  model={model}",
            f"tools ({len(tools)}): {', '.join(tools)}",
            "", "## USER MESSAGE", (user_text or "").strip(),
            "", "## SYSTEM PROMPT", "```", system or "", "```",
            "", "## MESSAGE HISTORY (input to the model)", "```json",
            _json.dumps(history, indent=2, default=str)[:80000], "```",
        ])
        with open(os.path.join(d, f"{ts}_{run_id}.md"), "w") as f:
            f.write(body)
        # JSON sidecar — same payload, structured, for the drawer's Context tab.
        with open(os.path.join(d, f"{ts}_{run_id}.json"), "w") as f:
            _json.dump({
                "run_id": run_id, "ts": ts, "thread_id": thread_id, "model": model,
                "focus_entity_id": focus_entity_id, "tools": tools,
                "user_text": user_text or "",
                "system": system or "",
                "history": history,
            }, f, default=str)
        # Rolling live transcript: a USER header per turn; events appended by
        # live_log_event. Tail this file to watch any run (incl. browser) live.
        with open(os.path.join(d, "live.log"), "a") as f:
            f.write(f"\n===== {ts}  run {run_id}  (thread {thread_id}) =====\n"
                    f"👤 {(user_text or '').strip()}\n")
    except Exception:  # noqa: BLE001 — a debug dump must never break a turn
        pass


def live_log_event(run_id, obj: dict, dtbuf: list) -> None:
    """Append one compact line per SSE event to the rolling live transcript
    (ABA_TURN_LOG_DIR/live.log), so an active run can be watched by tailing it.
    Streamed text 'delta' chunks are buffered and flushed as one line on the
    next non-delta event. Never raises."""
    d = _log_dir()
    if not d:
        return
    try:
        import os, json as _j
        os.makedirs(d, exist_ok=True)
        t = obj.get("type")
        # Full-fidelity record: append the UNTRUNCATED event (tool inputs +
        # results, plans, errors) to a per-run JSONL, so a run can be fully
        # reconstructed offline for debugging (the truncated live.log hid the
        # evidence when diagnosing the pagoda2 fabrication). 'delta' text chunks
        # are skipped — the prose is in live.log (coalesced) + the message log.
        if t and t != "delta" and t not in ("manifest", "usage", "suggestion_logged"):
            try:
                with open(os.path.join(d, f"{run_id}.jsonl"), "a") as _ff:
                    _ff.write(_j.dumps(obj, default=str) + "\n")
            except Exception:  # noqa: BLE001
                pass
        if t == "delta":
            dtbuf.append(obj.get("text") or "")
            return
        out: list[str] = []
        if dtbuf:
            txt = "".join(dtbuf).strip()
            dtbuf.clear()
            if txt:
                out.append(f"🗣  {txt}")
        if t == "tool_start":
            out.append(f"🔧 {obj.get('name')}  {_j.dumps(obj.get('input') or {}, default=str)[:220]}")
        elif t == "tool_result":
            out.append(f"   ✓ {_j.dumps(obj.get('result') or {}, default=str)[:220]}")  # noqa: seam — false positive: tool-result dict key, not the entity type
        elif t == "tool_progress":
            out.append(f"   ⏳ {str(obj.get('message'))[:160]}")
        elif t == "plan":
            out.append(f"📋 PLAN: {str(obj.get('title') or '')[:140]}")
        elif t in ("notice", "error", "cancelled", "clarification_pending", "approval_pending"):
            out.append(f"[{t}] {_j.dumps(obj, default=str)[:220]}")
        elif t == "entity_registered":
            e = obj.get("entity") or {}
            out.append(f"📦 {e.get('type')}: {e.get('title')}")
        elif t == "job_submitted":
            out.append(f"⚙ job: {_j.dumps(obj.get('job') or {}, default=str)[:160]}")
        elif t == "done":
            out.append("── turn done ──")
        # manifest / usage / suggestion_logged: skip (noise)
        if not out:
            return
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        with open(os.path.join(d, "live.log"), "a") as f:
            for ln in out:
                f.write(f"{ts} {ln}\n")
    except Exception:  # noqa: BLE001
        pass
