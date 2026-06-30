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
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

FEEDBACK_TO = os.getenv("ABA_FEEDBACK_EMAIL", "pk.restricted@gmail.com")
# Body budget: ~1050 chars keeps the encoded mailto URL near ~1750 (under the
# ~1800 cross-client safe ceiling) — measured ratio ≈1.7. Raised from 900 once we
# saw 900 force-trim the verbatim error + suggested fix out of real reports.
BODY_BUDGET = 1050


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

    # Trim to fit, lowest-value first. For the BUGFIXER the gold is the diagnosis
    # (cause + fix) and the verbatim error_tail; the repro/context prose is the
    # most compressible. So compress repro first (floor ~80), then error (floor
    # ~160 — keep the key line), then diagnosis (floor ~240 — keep cause + fix).
    def clip(t: str, n: int) -> str:
        return (t[:max(0, n - 1)].rstrip() + "…") if len(t) > n else t

    s, d, e = what_doing, diagnosis, error_tail
    for _ in range(8):
        body = build(s, d, e)
        if len(body) <= budget:
            return body
        over = len(body) - budget
        if len(s) > 80:       s = clip(s, max(80, len(s) - over))
        elif len(e) > 160:    e = clip(e, max(160, len(e) - over))
        elif len(d) > 240:    d = clip(d, max(240, len(d) - over))
        elif s:               s = ""          # last resort: drop repro prose
        else:                 return body[:budget - 1] + "…"
    return build(s, d, e)[:budget - 1] + "…"


_LOG_FILES = {"backend": "backend.log", "installer": "installer.log", "install": "installer.log",
              "helper": "../installer/helper.err.log"}


def read_aba_logs_impl(input_: dict, ctx: dict | None = None) -> dict:
    """Surface a redacted, size-capped slice of ABA's own logs TO GUIDE so it can
    find the real error and summarize the cause into a bug report. Reads only
    files under $ABA_HOME/logs (+ the helper err log); never arbitrary paths.

    which defaults to 'all' — searches every log and tags each line by source
    ([backend]/[installer]/[helper]) — so Guide doesn't have to guess which log a
    failure landed in (it reliably guesses wrong). Narrow to one only if asked."""
    which = (input_.get("which") or "all").strip().lower()
    if which == "all":
        sources = [("backend", "backend.log"), ("installer", "installer.log"),
                   ("helper", "../installer/helper.err.log")]
    elif which in _LOG_FILES:
        sources = [(which, _LOG_FILES[which])]
    else:
        return {"error": f"unknown log '{which}'; use 'all' or one of {sorted(set(_LOG_FILES))}"}
    try:
        tail = max(1, min(int(input_.get("tail") or 80), 400))
    except (TypeError, ValueError):
        tail = 80
    grep = (input_.get("grep") or "").strip().lower()

    home = Path(os.getenv("ABA_HOME", str(Path.home() / ".aba")))
    home_res = str(home.resolve())
    lines: list[str] = []
    by_source: dict[str, int] = {}
    for name, fname in sources:
        log = (home / "logs" / fname).resolve()
        if not str(log).startswith(home_res) or not log.exists():
            by_source[name] = 0
            continue
        try:
            rows = log.read_text(errors="replace").splitlines()
        except Exception:  # noqa: BLE001
            by_source[name] = 0
            continue
        # Drop the agent's own tool-call telemetry ("[feed] TOOL …") — it's not a
        # failure and otherwise drowns the real error when grepping for one.
        rows = [ln for ln in rows if "[feed] TOOL " not in ln]
        if grep:
            rows = [ln for ln in rows if grep in ln.lower()]
        rows = rows[-tail:]
        by_source[name] = len(rows)
        for ln in rows:
            lines.append(f"[{name}] " + _redact(ln))

    text = "\n".join(lines)
    if len(text) > 6000:                 # keep Guide's context manageable
        lines = text[-6000:].splitlines()
    return {
        "ok": True, "which": which, "matched_by_source": by_source, "lines": lines,
        "_agent_hint": ("Lines are tagged by source. Find the actual error/root cause "
                        "and SUMMARIZE it (note which log) for the report's diagnosis. "
                        "Do NOT paste raw log lines into build_bug_report — it's size-capped."),
    }


# ── client-side (browser) context stash (B4) ───────────────────────────────
# Guide can't see the browser; the bug button POSTs a snapshot here and Guide
# reads it via read_client_context to diagnose UI-only failures. A single latest
# snapshot is enough (per-install, single-user).
_CLIENT_CTX: dict = {}


def stash_client_context(context: dict | None) -> None:
    _CLIENT_CTX["latest"] = {"ts": time.time(), "context": context or {}}


def read_client_context_impl(input_: dict | None = None, ctx: dict | None = None) -> dict:
    entry = _CLIENT_CTX.get("latest")
    if not entry:
        return {"ok": True, "found": False,
                "note": ("No browser context captured. It's stashed when the user clicks "
                         "'Report a bug' in the header; if this is a UI issue, ask them to.")}
    c = entry.get("context") or {}
    errors = [_redact(str(e))[:300] for e in (c.get("errors") or [])][:30]
    return {
        "ok": True, "found": True,
        "captured_ago_s": int(time.time() - entry["ts"]),
        "route": _redact(str(c.get("route") or "")),
        "section": c.get("section"),
        "focused_type": c.get("focusedType"),
        "console_errors": errors,
        "user_agent": (c.get("userAgent") or "")[:140],
        "_agent_hint": ("Browser-side evidence (you can't see the UI directly). "
                        "Summarize the relevant console error + where it happened into "
                        "the bug report's diagnosis; do not paste raw lines."),
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
    # Locators for controlled deployments (e.g. VBC): short node id + login + project
    # let us SSH in and introspect the real ~/.aba/logs. User-visible (the whole
    # email is reviewed before sending), so consented; short node only (no FQDN).
    node = (platform.node() or "?").split(".")[0]
    try:
        import getpass
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = os.getenv("USER") or "?"
    pid = ctx.get("project_id")
    if not pid:
        try:
            from core import projects as _proj
            pid = _proj.current()
        except Exception:  # noqa: BLE001
            pid = None
    # Compact, slash-delimited locator line (no labels — it's for the agent/parser,
    # not the user). Positional schema:
    #   commit / os release / arch / node / user / project / model / thread / focus / report-id
    ctxline = (f"— {_aba_commit()}/{platform.system()} {platform.release()}/{platform.machine()}/"
               f"{node}/{user}/{pid or '—'}/{os.getenv('ABA_MODEL', '?')}/{tid}/{focus}/{rid}")

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
