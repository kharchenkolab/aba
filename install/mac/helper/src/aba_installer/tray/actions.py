"""Tray action handlers — Start / Stop / Restart / Open / Check updates /
Show logs / Kickstart helper.

Each handler talks to the helper service over loopback (no privilege required)
or, for ``kickstart_helper``, to ``launchctl`` directly. Returns a uniform
``ActionResult`` the menu can show as a one-line toast or status message —
specifically including a *helper offline* error mode that points the user at
the right next move (kickstart the LaunchAgent) rather than just saying
"failed"."""
from __future__ import annotations
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


# The helper LaunchAgent's label — kept in sync with launchagent.LABEL.
# Don't import that module here; we want this file to stay importable
# inside the tray process before the full helper package finishes loading.
_HELPER_LABEL = "com.kharchenkolab.aba.helper"

_BACKEND_URL = "http://127.0.0.1:8000/"


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str = ""        # one-line summary the menu can toast


# ─── HTTP helper ──────────────────────────────────────────────────────────
def _post(path: str, *, port: int, urlopen: Callable,
          timeout_s: float = 30.0) -> tuple[bool, Any, Optional[str]]:
    """POST <path> with no body. Returns (ok, parsed_response, error_message).
    The third tuple element is the *user-facing* error message — not just an
    exception repr — so handlers don't have to rebuild it."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="POST",
                                 data=b"", headers={"Content-Length": "0"})
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        return False, None, f"helper rejected {path} (HTTP {e.code})"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False, None, ("Helper offline — start it from the menu "
                             "('Start helper…') or System Settings → "
                             "Login Items.")
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return False, None, f"helper returned non-JSON from {path}"
    return True, parsed, None


# ─── start / stop / restart ──────────────────────────────────────────────
def start(*, port: int,
          urlopen: Callable = urllib.request.urlopen) -> ActionResult:
    ok, parsed, err = _post("/api/start", port=port, urlopen=urlopen)
    if not ok:
        return ActionResult(False, err or "")
    msg = parsed.get("stdout") or "Started." if isinstance(parsed, dict) else "Started."
    return ActionResult(True, str(msg).strip())


def stop(*, port: int,
         urlopen: Callable = urllib.request.urlopen) -> ActionResult:
    ok, parsed, err = _post("/api/stop", port=port, urlopen=urlopen)
    if not ok:
        return ActionResult(False, err or "")
    return ActionResult(True, "Stopped.")


def restart(*, port: int,
            urlopen: Callable = urllib.request.urlopen) -> ActionResult:
    """Stop, then start. Don't try to start something that just refused to
    stop — the user needs to see what's wrong with stop, not have it masked
    by a downstream start error."""
    s = stop(port=port, urlopen=urlopen)
    if not s.ok:
        return ActionResult(False, f"stop failed: {s.message}")
    return start(port=port, urlopen=urlopen)


# ─── open in browser ─────────────────────────────────────────────────────
def open_abc_browser(*, open_url: Callable[[str], None]) -> ActionResult:
    """Open the ABA app (backend SPA) at :8000 in the default browser."""
    open_url(_BACKEND_URL)
    return ActionResult(True, _BACKEND_URL)


def check_updates(*, port: int,
                  open_url: Callable[[str], None]) -> ActionResult:
    """Delegate the multi-step update to the helper's existing browser
    Control page — it already renders the SSE step list + live log. The tray
    just opens the right URL; the user watches there. See misc/mac-install.md
    § 3c.3 (the design choice not to rebuild that UI in Cocoa)."""
    url = f"http://127.0.0.1:{port}/"
    open_url(url)
    return ActionResult(True, url)


# ─── show logs ───────────────────────────────────────────────────────────
def show_logs(*, port: int, log_dir: Path,
              urlopen: Callable = urllib.request.urlopen,
              open_path: Callable[[Path], None]) -> ActionResult:
    """Pull recent backend log lines from the helper, write them to a
    ``log_dir``-relative file, and open that file with the OS's default
    handler (TextEdit, Console.app, or whatever the user mapped to .log).

    Writing to a file rather than streaming into a rumps window keeps the
    tray's GUI surface tiny — and lets the user keep the log open in a real
    window while they do something else."""
    url = f"http://127.0.0.1:{port}/api/logs?tail=200"
    req = urllib.request.Request(url)
    try:
        with urlopen(req, timeout=5.0) as resp:
            body = resp.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return ActionResult(False, "Helper offline — can't read logs.")
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return ActionResult(False, "Garbage response from /api/logs.")
    lines = parsed.get("lines") or [] if isinstance(parsed, dict) else []
    log_dir.mkdir(parents=True, exist_ok=True)
    # Stable name (we overwrite per click — users don't want a directory full
    # of one-off log dumps; the helper's own backend.log is the persistent record).
    dest = log_dir / "tray-backend-tail.log"
    dest.write_text("\n".join(lines) + "\n")
    open_path(dest)
    return ActionResult(True, str(dest))


# ─── kickstart the helper LaunchAgent ────────────────────────────────────
def _gui_target() -> str:
    """``gui/<uid>/<label>`` is the modern launchctl target. Use it; older
    macOS versions accept it too."""
    return f"gui/{os.getuid()}/{_HELPER_LABEL}"


def kickstart_helper(*, run: Callable = subprocess.run) -> ActionResult:
    """``launchctl kickstart`` the helper LaunchAgent. Used when the tray
    poller reports ``helper_offline`` and the user clicks 'Start helper…'.

    No privilege escalation — user-level LaunchAgents take ``gui/<uid>/...``
    and need no admin."""
    argv = ["launchctl", "kickstart", "-k", _gui_target()]
    try:
        proc = run(argv, capture_output=True, text=True, timeout=10)
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"launchctl error: {e}")
    if proc.returncode != 0:
        # The most common failure here is "Operation not permitted" when the
        # plist isn't registered (the helper LaunchAgent was never installed).
        return ActionResult(False, (proc.stderr or proc.stdout or "").strip()
                            or f"launchctl exited {proc.returncode}")
    return ActionResult(True, "Helper started.")
