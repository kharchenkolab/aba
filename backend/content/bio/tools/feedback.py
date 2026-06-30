"""Bug-report feedback tool impl (misc/feedback.md).

Guide composes the human-readable parts (what happened, its diagnosis); this
stamps the system facts (ABA commit, OS/arch, model, thread/focus ids), enforces
the ~900-char email-body budget, redacts, and returns a ready-to-click `mailto:`
URL. The user's own mail client does the send (no server, no key) to the team
inbox; replies route follow-up back through Guide. Body layout: human-readable
lead first, terse machine-parsable context tail (an agent reads the inbox)."""
from __future__ import annotations
import os
import platform
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

FEEDBACK_TO = os.getenv("ABA_FEEDBACK_EMAIL", "pk.restricted@gmail.com")
BODY_BUDGET = 900          # keeps the encoded mailto URL well under the ~1800 safe ceiling


def _redact(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"/Users/[^/\s]+", "~", s)                 # home dir
    s = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "<token>", s)    # anthropic tokens
    return s.strip()


def _aba_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent),
                              "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return (out.stdout or "").strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _report_id() -> str:
    import secrets
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%MZ-") + secrets.token_hex(2)


def _assemble(headline: str, what_doing: str, diagnosis: str, error_tail: str, ctxline: str,
              budget: int = BODY_BUDGET) -> str:
    lead = "[Add anything you'd like us to know above this line.]\n\n"
    foot = ("\nNeed more? Reply — we'll have Guide (in your ABA) pull the exact "
            "detail you ask for.\n" + ctxline)

    # Bugfixer-facing labels (the reader is a developer/agent, not the user).
    def build(s, d, e):
        parts = [f"🪲 {headline}"]
        if s: parts.append(f"Repro/context: {s}")
        if d: parts.append(f"\nDiagnosis: {d}")
        if e: parts.append(f"\nError: {e}")
        return lead + "\n".join(parts) + "\n" + foot

    s, d, e = what_doing, diagnosis, error_tail
    for _ in range(5):                 # trim low-priority sections until it fits
        body = build(s, d, e)
        if len(body) <= budget:
            return body
        if e:                 e = ""
        elif len(d) > 220:    d = d[:217].rstrip() + "…"
        elif len(s) > 140:    s = s[:137].rstrip() + "…"
        elif d:               d = ""
        elif s:               s = ""
        else:                 return body[:budget - 1] + "…"
    return build(s, d, e)[:budget - 1] + "…"


_LOG_FILES = {"backend": "backend.log", "installer": "installer.log", "install": "installer.log",
              "helper": "../installer/helper.err.log"}


def read_aba_logs_impl(input_: dict, ctx: dict | None = None) -> dict:
    """Surface a redacted, size-capped slice of ABA's own logs TO GUIDE so it can
    find the real error and summarize the cause into a bug report. Reads only
    files under $ABA_HOME/logs (+ the helper err log); never arbitrary paths."""
    which = (input_.get("which") or "backend").strip().lower()
    fname = _LOG_FILES.get(which)
    if not fname:
        return {"error": f"unknown log '{which}'; choose one of {sorted(set(_LOG_FILES))}"}
    try:
        tail = max(1, min(int(input_.get("tail") or 80), 400))
    except (TypeError, ValueError):
        tail = 80
    grep = (input_.get("grep") or "").strip().lower()

    home = Path(os.getenv("ABA_HOME", str(Path.home() / ".aba")))
    log = (home / "logs" / fname).resolve()
    # Containment: must stay within ABA_HOME (the ".." helper path resolves under it).
    if not str(log).startswith(str(home.resolve())):
        return {"error": "refusing to read outside ABA_HOME"}
    if not log.exists():
        return {"ok": True, "which": which, "lines": [], "note": "log not found / empty"}
    try:
        rows = log.read_text(errors="replace").splitlines()
    except Exception as e:  # noqa: BLE001
        return {"error": f"could not read {fname}: {e}"}

    if grep:
        rows = [ln for ln in rows if grep in ln.lower()]
    sel = [_redact(ln) for ln in rows[-tail:]]
    text = "\n".join(sel)
    if len(text) > 6000:                 # keep Guide's context manageable
        text = text[-6000:]
        sel = text.splitlines()
    return {
        "ok": True, "which": which, "matched": len(sel), "lines": sel,
        "_agent_hint": ("Find the actual error/root cause in these lines and "
                        "SUMMARIZE it for the report's diagnosis. Do NOT paste raw "
                        "log lines into build_bug_report — the email is size-capped."),
    }


def build_bug_report_impl(input_: dict, ctx: dict | None = None) -> dict:
    """input_: {headline, what_doing?, diagnosis?, error_tail?}. Returns
    {ok, mailto_url, body, report_id}."""
    ctx = ctx or {}
    headline = _redact(input_.get("headline") or "")
    if not headline:
        return {"error": "headline is required — a one-line, plain-language summary of what went wrong."}
    what_doing = _redact(input_.get("what_doing") or "")
    diagnosis = _redact(input_.get("diagnosis") or "")
    error_tail = _redact(input_.get("error_tail") or "")

    rid = _report_id()
    tid = ctx.get("thread_id") or "—"
    focus = ctx.get("focus_entity_id") or "—"
    ctxline = (f"— ABA {_aba_commit()} · {platform.system()} {platform.release()} "
               f"{platform.machine()} · model {os.getenv('ABA_MODEL', '?')} · "
               f"thr {tid} · focus {focus} · {rid}")

    body = _assemble(headline, what_doing, diagnosis, error_tail, ctxline)
    # Lead the subject with 🪲 so the team can filter on it (matches the bug
    # glyph used in the UI; plain text can't carry the SVG, so this is the
    # closest stand-in — deliberately not the 🐛 caterpillar).
    subject = (f"🪲 ABA bug: {headline}")[:80]
    q = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    mailto_url = f"mailto:{FEEDBACK_TO}?{q}".replace("%0A", "%0D%0A")

    return {
        "ok": True,
        "report_id": rid,
        "mailto_url": mailto_url,
        "body": body,   # for the agent to show as a preview if it wants
        "_agent_hint": ("Present mailto_url to the user as a markdown link titled "
                        "'Review & send bug report' (do NOT paste the raw URL, and "
                        "do NOT add an emoji — the UI renders the bug icon); say "
                        "it opens their email prefilled, with a blank space at the top "
                        "for their own note. Remember the report is read by an ABA "
                        "developer/bugfixer — it captured the technical diagnosis, not "
                        "user advice. Any guidance for the USER (e.g. workarounds) goes "
                        "in your chat reply, NOT in the report."),
    }
