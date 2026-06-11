"""Tier-0-tray action handlers — start/stop/restart/open/check_updates/show_logs.

Each handler hits the right /api/* endpoint with the right HTTP method, and
maps the helper's response (or a transport error) into a uniform
ActionResult the menu can show as a toast.
"""
from __future__ import annotations
import json
import urllib.error
from pathlib import Path

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
def _ok_resp(req=None, timeout=None):
    """Stub urlopen that returns a 200 for any GET."""
    return _StubResp({"ok": True})


def test_check_updates_happy_path_opens_browser_when_control_page_ok():
    """Step 1 of the cascade: '/' returns 2xx → open browser, done."""
    opened: list = []
    res = actions.check_updates(
        port=8765,
        open_url=lambda u: opened.append(u),
        urlopen=_ok_resp,
    )
    assert res.ok
    assert opened == ["http://127.0.0.1:8765/"]


def test_check_updates_kickstarts_when_control_page_is_5xx():
    """The 2026-06-11 bug shape: /api/status is fine but '/' returns
    500 from a stale StaticFiles path. The smart action MUST probe '/'
    (the actual URL it forwards to), not /api/status. On 500 it
    kickstarts the helper and re-probes; if '/' is healthy after the
    restart it opens the browser normally."""
    # First probe of '/' raises a 500 HTTPError; subsequent probes (post-
    # kickstart) succeed.
    calls = iter([
        urllib.error.HTTPError("http://127.0.0.1:8765/", 500,
                                "Internal Server Error", None, None),
        _StubResp({"ok": True}),
    ])
    def fake_urlopen(req, timeout=None):
        nxt = next(calls)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    launchctl_calls: list = []
    def fake_run(argv, **kw):
        launchctl_calls.append(argv)
        import subprocess
        return subprocess.CompletedProcess(argv, 0, "", "")

    opened: list = []
    notes: list = []
    res = actions.check_updates(
        port=8765,
        open_url=lambda u: opened.append(u),
        urlopen=fake_urlopen,
        run=fake_run,
        sleep=lambda _s: None,
        notify=lambda t, sub, body: notes.append((t, sub, body)),
    )
    assert res.ok, res.message
    assert launchctl_calls and launchctl_calls[0][0] == "launchctl"
    assert opened == ["http://127.0.0.1:8765/"]
    # User saw a notification explaining the restart — no silent flicker.
    assert any("Reviving" in sub for _, sub, _ in notes), notes


def test_check_updates_falls_back_to_inline_when_kickstart_doesnt_help(
        tmp_path, monkeypatch):
    """Step 3 of the cascade: kickstart didn't fix '/', so run the
    inline update transparently. The user clicks ONE item; the inline
    path fires automatically with a notification."""
    # All '/' probes return 500.
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 500, "Internal Server Error", None, None)
    def fake_run(argv, **kw):
        import subprocess
        return subprocess.CompletedProcess(argv, 0, "", "")

    # Stub Executor so the test doesn't actually pull repos / restart anything.
    class FakeExecutor:
        def __init__(self, pb, on_event=None, **kw):
            self.on_event = on_event
        def run_all(self, only=None):
            self.on_event("step_start", {"step_id": "pull-aba",
                                           "title": "Update aba"})
            self.on_event("step_end", {"step_id": "pull-aba", "ok": True})
            class _R: ok = True; error = None
            return [_R()]
    monkeypatch.setattr("aba_installer.playbook.Executor", FakeExecutor)
    # Force the inline log into tmp_path so the test isn't dirty.
    monkeypatch.setattr(actions, "_inline_log_path",
                         lambda: tmp_path / "tray-update.log")
    # Reset the inflight flag in case a prior test left it set.
    actions._INLINE_UPDATE_INFLIGHT = False

    class _Sync:
        def __init__(self, target): self._t = target
        def start(self): self._t()

    opened_urls: list = []
    opened_paths: list = []
    notes: list = []
    res = actions.check_updates(
        port=8765,
        open_url=lambda u: opened_urls.append(u),
        open_path=lambda p: opened_paths.append(Path(p)),
        urlopen=fake_urlopen,
        run=fake_run,
        sleep=lambda _s: None,
        notify=lambda t, sub, body: notes.append((t, sub, body)),
        thread_factory=lambda target: _Sync(target),
    )
    assert res.ok, res.message
    # Browser was NEVER opened (control page stayed broken)
    assert opened_urls == []
    # Log file was opened so the user can watch
    assert opened_paths == [tmp_path / "tray-update.log"]
    # User got the "running inline" notification (the explanation of why
    # the browser didn't pop up).
    assert any("inline" in sub.lower() or "inline" in body.lower()
               for _, sub, body in notes), notes
    # And the inline run actually executed: log file exists with the
    # FakeExecutor's events.
    contents = (tmp_path / "tray-update.log").read_text()
    assert "Update aba" in contents
    assert "DONE OK" in contents


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


# ─── update_inline: the helper-free escape hatch ──────────────────────────
def test_update_inline_loads_playbook_and_spawns_worker(tmp_path,
                                                         monkeypatch):
    """Inline update reads the package's update.yml, hands it to Executor,
    and spawns a daemon thread. The test runs the worker synchronously
    via thread_factory so we can assert on the log shape.

    What we pin (the 'this is the actual fallback' contract):
      - the log file is created at the requested path,
      - the file gets opened for the user to watch,
      - a notification fires on completion,
      - the inflight flag releases (so a second call doesn't lock out).
    """
    import threading
    # Stub Executor so the test doesn't actually pull repos / build
    # frontend / restart backend on the dev machine.
    class FakeExecutor:
        def __init__(self, pb, on_event=None, **kw):
            self.pb = pb
            self.on_event = on_event
        def run_all(self, only=None):
            self.on_event("step_start", {"step_id": "pull-aba",
                                           "title": "Update aba"})
            self.on_event("command_output", {"step_id": "pull-aba",
                                              "line": "Already up to date."})
            self.on_event("step_end", {"step_id": "pull-aba", "ok": True})
            class _R:
                ok = True
                error = None
            return [_R()]
    monkeypatch.setattr("aba_installer.playbook.Executor", FakeExecutor)

    # Make threading run the worker inline so the test can inspect state.
    class _Sync:
        def __init__(self, target): self._target = target
        def start(self): self._target()
    log = tmp_path / "tray-update.log"

    opened: list = []
    notes: list = []
    res = actions.update_inline(
        open_path=lambda p: opened.append(Path(p)),
        notify=lambda t, sub, body: notes.append((t, sub, body)),
        log_path=log,
        thread_factory=lambda target: _Sync(target),
    )
    assert res.ok, res.message
    assert opened == [log], "log must open immediately so user can tail it"
    # Worker ran synchronously and wrote events
    contents = log.read_text()
    assert "Update aba" in contents
    assert "Already up to date." in contents
    assert "DONE OK" in contents
    # Notification fired with the OK status
    assert notes and notes[-1][1] == "OK"
    # Inflight flag released so another invocation isn't blocked
    assert actions._INLINE_UPDATE_INFLIGHT is False


def test_update_inline_refuses_when_one_is_already_running(tmp_path):
    """Second click while a run is in flight must return a clean error,
    not double-spawn the playbook (which would race on git pull / npm
    ci output)."""
    # Force the inflight flag without going through a real run.
    actions._INLINE_UPDATE_INFLIGHT = True
    try:
        res = actions.update_inline(
            open_path=lambda p: None,
            log_path=tmp_path / "x.log",
            thread_factory=lambda target: type("T", (),
                                               {"start": lambda self: None})(),
        )
        assert not res.ok
        assert "already running" in (res.message or "").lower()
    finally:
        actions._INLINE_UPDATE_INFLIGHT = False
