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

    def build(s, d, e):
        parts = [f"🐛 {headline}"]
        if s: parts.append(f"What I was doing: {s}")
        if d: parts.append(f"\nGuide's read: {d}")
        if e: parts.append(f"\n{e}")
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
    subject = (f"ABA bug: {headline}")[:80]
    q = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    mailto_url = f"mailto:{FEEDBACK_TO}?{q}".replace("%0A", "%0D%0A")

    return {
        "ok": True,
        "report_id": rid,
        "mailto_url": mailto_url,
        "body": body,   # for the agent to show as a preview if it wants
        "_agent_hint": ("Present mailto_url to the user as a markdown link titled "
                        "'🐛 Review & send bug report', and tell them it opens their "
                        "email with the report prefilled — they can add a comment at "
                        "the top and hit send. Do NOT paste the raw URL."),
    }
