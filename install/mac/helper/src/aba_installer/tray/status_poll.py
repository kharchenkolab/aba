"""Status poller — normalize the helper's /api/status JSON into a single UI
state the menu-bar renderer + enabled-state wiring can read off.

Crucial distinction: ``helper_offline`` (LaunchAgent stopped or crashed —
loopback connection refused) vs ``stopped`` (helper says
``backend_running=False``). The two need different remediation from the
menu: helper_offline → ``launchctl kickstart``; stopped → ``POST /api/start``.
A flat "down" state would be a bug factory.
"""
from __future__ import annotations
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional


# ─── shape the menu reads ──────────────────────────────────────────────────
@dataclass(frozen=True)
class TrayStatus:
    """One snapshot of derived UI state for the menu-bar item."""
    state: str          # running | stopped | installing | updating |
                        # not_installed | no_credentials | helper_offline
    label: str          # the one-line status row (with leading glyph)
    pid: Optional[int]  # backend pid when running, else None
    can_start: bool     # enable the Start menu item?
    can_stop: bool      # enable Stop / Restart?
    can_open: bool      # enable "Open ABA →" (backend serving at :8000)


# ─── pure normalization (the load-bearing logic) ───────────────────────────
def _to_tray_status(payload: dict) -> TrayStatus:
    """Map /api/status JSON → TrayStatus. Pure function; the layer above can
    test it without HTTP."""
    installed = bool(payload.get("installed"))
    backend_running = bool(payload.get("backend_running"))
    pid = payload.get("backend_pid")
    op = (payload.get("operation") or "").strip().lower() or None
    has_creds = bool(payload.get("credentials"))

    # Long-running operations dominate — Start/Stop are inert until the
    # operation completes, the backend bounces, and the next poll resolves.
    if op == "install":
        return TrayStatus(state="installing",
                          label="⏳  Installing…",
                          pid=None, can_start=False, can_stop=False, can_open=False)
    if op == "update":
        return TrayStatus(state="updating",
                          label="⏳  Updating…",
                          pid=pid if backend_running else None,
                          can_start=False, can_stop=False, can_open=False)

    if backend_running:
        return TrayStatus(state="running",
                          label=f"●  Running   pid {pid}",
                          pid=pid, can_start=False, can_stop=True, can_open=True)

    if not has_creds:
        # The user landed in setup.command but hasn't completed sign-in yet.
        # Point them at the browser Welcome page; nothing to start.
        return TrayStatus(state="no_credentials",
                          label="○  Sign in to ABA…",
                          pid=None, can_start=False, can_stop=False, can_open=False)

    if not installed:
        # Credentials present, install not finished. The Setup page is where
        # the user goes next; the menu surfaces that.
        return TrayStatus(state="not_installed",
                          label="○  Setup in progress…",
                          pid=None, can_start=False, can_stop=False, can_open=False)

    return TrayStatus(state="stopped",
                      label="○  Stopped",
                      pid=None, can_start=True, can_stop=False, can_open=False)


# ─── transport: pull /api/status with injected urlopen for tests ───────────
def fetch_status(*, port: int,
                 urlopen: Callable = urllib.request.urlopen,
                 timeout_s: float = 2.0) -> TrayStatus:
    """Fetch /api/status and map → TrayStatus. Any transport-layer failure
    (connection refused, timeout, garbage body) returns the canonical
    ``helper_offline`` state so the caller doesn't have to disambiguate."""
    url = f"http://127.0.0.1:{port}/api/status"
    try:
        req = urllib.request.Request(url)
        with urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
        payload = json.loads(body)
    except (urllib.error.URLError, socket.timeout, ConnectionError,
            TimeoutError, json.JSONDecodeError, ValueError):
        return TrayStatus(state="helper_offline",
                          label="⏸  Helper offline",
                          pid=None, can_start=False, can_stop=False, can_open=False)
    if not isinstance(payload, dict):
        return TrayStatus(state="helper_offline",
                          label="⏸  Helper offline",
                          pid=None, can_start=False, can_stop=False, can_open=False)
    return _to_tray_status(payload)


# ─── port discovery ────────────────────────────────────────────────────────
_DEFAULT_PORT = 8765      # matches setup.command's documented default


def helper_port() -> int:
    """Read the helper's chosen port from ``$ABA_HOME/installer/port.txt``
    (sticky across helper restarts; written by ``service.main`` at boot).
    Falls back to the documented 8765 default when the file is absent —
    that's the right behaviour when the tray launches before the helper
    has written port.txt for the first time."""
    from aba_installer.paths import port_file
    try:
        pf = port_file()
        if pf.exists():
            return int(pf.read_text().strip())
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_PORT
