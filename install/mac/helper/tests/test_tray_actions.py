"""Tier-0-tray action handlers — start/stop/restart/open/check_updates/show_logs.

Each handler hits the right /api/* endpoint with the right HTTP method, and
maps the helper's response (or a transport error) into a uniform
ActionResult the menu can show as a toast.
"""
from __future__ import annotations
import json
import urllib.error

import pytest

from aba_installer.tray import actions


class _StubResp:
    """Minimal urllib response stub used by every test."""
    def __init__(self, payload: dict, status: int = 200):
        self._b = json.dumps(payload).encode()
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._b


def _record(seen: list, payload: dict, status: int = 200):
    def fake(req, timeout=None):
        seen.append({"url": req.full_url,
                     "method": req.get_method(),
                     "data": req.data})
        return _StubResp(payload, status)
    return fake


# ─── start / stop / restart route to the right endpoints ────────────────
def test_start_posts_to_api_start():
    seen: list = []
    res = actions.start(port=8765, urlopen=_record(seen, {"ok": True, "stdout": "ABA up"}))
    assert res.ok
    assert seen[0]["url"].endswith("/api/start")
    assert seen[0]["method"] == "POST"
    assert "ABA up" in (res.message or "")


def test_stop_posts_to_api_stop():
    seen: list = []
    res = actions.stop(port=8765, urlopen=_record(seen, {"ok": True, "killed": 123}))
    assert res.ok
    assert seen[0]["url"].endswith("/api/stop")
    assert seen[0]["method"] == "POST"


def test_restart_is_stop_then_start():
    """The helper has no /api/restart endpoint, so the tray does it client-side
    — stop first, then start. Both must succeed, in order, for a clean
    restart; first failure aborts."""
    seen: list = []
    res = actions.restart(port=8765, urlopen=_record(seen, {"ok": True}))
    assert res.ok
    paths = [s["url"].rsplit("/", 1)[-1] for s in seen]
    assert paths == ["stop", "start"]


def test_restart_aborts_if_stop_fails():
    """Don't try to start something that just refused to stop — the user
    needs to see what's wrong with stop, not have it masked by a second
    error from start."""
    payloads = iter([
        urllib.error.HTTPError("/api/stop", 500, "boom", None, None),
        {"ok": True},   # never called
    ])
    seen: list = []
    def fake(req, timeout=None):
        seen.append(req.full_url)
        nxt = next(payloads)
        if isinstance(nxt, Exception):
            raise nxt
        return _StubResp(nxt)
    res = actions.restart(port=8765, urlopen=fake)
    assert not res.ok
    assert "stop" in res.message.lower()
    assert len(seen) == 1, f"start should not have been called, saw: {seen}"


# ─── check_updates streams the existing /api/update SSE — open browser ────
def test_check_updates_opens_browser_to_control_page():
    """rumps's main thread can't host a long-running SSE renderer well.
    The tray delegates the multi-step update flow to the helper's existing
    browser Control page — which already has the SSE renderer + step list.
    See misc/mac-install.md § 3c.3."""
    opened: list = []
    res = actions.check_updates(port=8765,
                                open_url=lambda u: opened.append(u))
    assert res.ok
    assert opened == ["http://127.0.0.1:8765/"]


def test_open_browser_targets_backend_port_8000():
    opened: list = []
    res = actions.open_abc_browser(open_url=lambda u: opened.append(u))
    assert res.ok
    assert opened == ["http://127.0.0.1:8000/"]


# ─── show_logs returns the recent helper backend logs ────────────────────
def test_show_logs_fetches_api_logs_and_writes_tempfile(tmp_path):
    """rumps doesn't have a built-in log window — we fetch /api/logs?tail=N,
    write to a temp file in $ABA_HOME/logs, and let the OS open it (TextEdit
    or whichever is associated with .log)."""
    seen: list = []
    payload = {"path": "/u/.aba/logs/backend.log",
               "lines": ["2026-06-10 line A", "2026-06-10 line B"]}
    opened: list = []
    res = actions.show_logs(port=8765, log_dir=tmp_path,
                            urlopen=_record(seen, payload),
                            open_path=lambda p: opened.append(str(p)))
    assert res.ok
    assert seen[0]["url"].endswith("/api/logs?tail=200")
    # We wrote a file the OS handler opened
    assert len(opened) == 1
    written = opened[0]
    assert written.startswith(str(tmp_path))
    assert (tmp_path / written.rsplit("/", 1)[-1]).read_text().endswith("line B\n")


# ─── helper offline → uniform actionable error message ───────────────────
def test_start_returns_actionable_error_on_helper_offline():
    """When loopback connection fails, the tray must NOT just say 'failed'.
    The cause is the helper LaunchAgent being down; the message has to
    invite the right next move (kickstart it)."""
    def fake(req, timeout=None):
        raise urllib.error.URLError("Connection refused")
    res = actions.start(port=8765, urlopen=fake)
    assert not res.ok
    assert "helper" in res.message.lower()


# ─── kickstart_helper triggers launchctl with the right label ────────────
def test_kickstart_helper_calls_launchctl_with_the_label():
    """When the helper is offline, the menu shows a 'Start helper…' action.
    That action ``launchctl kickstart``s the agent label so the user doesn't
    have to know about plists or System Settings → Login Items."""
    seen_argv: list = []
    def fake_run(argv, capture_output=True, text=True, timeout=None):
        seen_argv.append(argv)
        class _P:
            returncode = 0
            stdout = ""
            stderr = ""
        return _P()
    res = actions.kickstart_helper(run=fake_run)
    assert res.ok
    assert seen_argv[0][0] == "launchctl"
    # gui/<uid>/<LABEL> is the documented kickstart target; tolerate either
    # the leading "gui/<uid>/" prefix (modern launchctl) or the bare label
    # (older), as long as the helper's plist label is in there.
    target = seen_argv[0][-1]
    assert "com.kharchenkolab.aba.helper" in target


def test_kickstart_helper_reports_failure_with_stderr():
    def fake_run(argv, capture_output=True, text=True, timeout=None):
        class _P:
            returncode = 113
            stdout = ""
            stderr = "Operation not permitted"
        return _P()
    res = actions.kickstart_helper(run=fake_run)
    assert not res.ok
    assert "not permitted" in (res.message or "").lower()
