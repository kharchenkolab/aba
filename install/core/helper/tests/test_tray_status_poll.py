"""Tier-0-tray status poller: normalizes the helper's /api/status JSON into a
single UI state the menu-bar handlers + enabled-state wiring can read off."""
from __future__ import annotations
import json
import urllib.error

import pytest

from aba_installer.tray import status_poll as sp


# ─── _to_tray_status: pure normalization ──────────────────────────────────
def test_running_when_backend_alive():
    s = sp._to_tray_status({
        "aba_home": "/u/.aba", "installed": True,
        "backend_running": True, "backend_pid": 12345,
        "operation": None, "credentials": True, "launcher": "/u/.aba/bin/aba",
    })
    assert s.state == "running"
    assert s.pid == 12345
    assert s.label.startswith("●") and "pid 12345" in s.label
    # enabled-state for menu items: only Stop/Restart when running
    assert s.can_start is False
    assert s.can_stop is True
    assert s.can_open is True


def test_stopped_when_installed_but_not_running():
    s = sp._to_tray_status({
        "aba_home": "/u/.aba", "installed": True,
        "backend_running": False, "backend_pid": None,
        "operation": None, "credentials": True, "launcher": "/u/.aba/bin/aba",
    })
    assert s.state == "stopped"
    assert s.label.startswith("○")
    assert s.can_start is True
    assert s.can_stop is False
    assert s.can_open is False     # nothing to open at :8000 when backend is down


def test_installing_when_op_install():
    s = sp._to_tray_status({
        "aba_home": "/u/.aba", "installed": False,
        "backend_running": False, "backend_pid": None,
        "operation": "install", "credentials": True, "launcher": None,
    })
    assert s.state == "installing"
    assert s.label.startswith("⏳")
    # Start/Stop both disabled while a long op is in flight
    assert s.can_start is False
    assert s.can_stop is False


def test_updating_when_op_update():
    s = sp._to_tray_status({
        "aba_home": "/u/.aba", "installed": True,
        "backend_running": True, "backend_pid": 999,
        "operation": "update", "credentials": True, "launcher": "/u/.aba/bin/aba",
    })
    assert s.state == "updating"
    # During an update the backend will bounce; both controls go inactive
    assert s.can_start is False
    assert s.can_stop is False


def test_not_installed_blocks_start():
    s = sp._to_tray_status({
        "aba_home": "/u/.aba", "installed": False,
        "backend_running": False, "backend_pid": None,
        "operation": None, "credentials": True, "launcher": None,
    })
    assert s.state == "not_installed"
    assert s.can_start is False        # no launcher to fire
    assert s.can_stop is False


def test_no_credentials_distinct_state():
    """When the user hasn't completed sign-in yet, surface that specifically
    so the menu can point them at the browser Welcome page."""
    s = sp._to_tray_status({
        "aba_home": "/u/.aba", "installed": False,
        "backend_running": False, "backend_pid": None,
        "operation": None, "credentials": False, "launcher": None,
    })
    assert s.state == "no_credentials"
    assert "sign in" in s.label.lower()


# ─── fetch_status: HTTP + transport-error handling ─────────────────────────
class _StubResp:
    def __init__(self, payload: dict, status: int = 200):
        self._b = json.dumps(payload).encode()
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._b


def test_fetch_status_decodes_helper_response():
    payload = {"aba_home": "/u/.aba", "installed": True,
               "backend_running": True, "backend_pid": 42,
               "operation": None, "credentials": True, "launcher": "/u/.aba/bin/aba"}
    def fake_urlopen(req, timeout=None):
        return _StubResp(payload)
    s = sp.fetch_status(port=8765, urlopen=fake_urlopen)
    assert s.state == "running" and s.pid == 42


def test_fetch_status_returns_helper_offline_on_connection_refused():
    """Tray must distinguish a downed HELPER (LaunchAgent stopped / crashed)
    from a downed BACKEND (helper says backend_running=False). They take
    different remediation paths from the menu — start backend vs. kickstart
    the LaunchAgent."""
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    s = sp.fetch_status(port=8765, urlopen=fake_urlopen)
    assert s.state == "helper_offline"
    assert s.label.startswith("⏸") and "helper offline" in s.label.lower()
    assert s.can_start is False
    assert s.can_stop is False


def test_fetch_status_returns_helper_offline_on_socket_timeout():
    def fake_urlopen(req, timeout=None):
        import socket
        raise socket.timeout("read timeout")
    s = sp.fetch_status(port=8765, urlopen=fake_urlopen)
    assert s.state == "helper_offline"


def test_fetch_status_returns_helper_offline_on_garbage_response():
    """If the helper returned 200 with a non-JSON body, treat as offline —
    don't try to render an undefined state."""
    class _Garbage:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<html>oops</html>"
    def fake_urlopen(req, timeout=None):
        return _Garbage()
    s = sp.fetch_status(port=8765, urlopen=fake_urlopen)
    assert s.state == "helper_offline"


def test_port_resolution_reads_port_file(tmp_path, monkeypatch):
    """The tray launches separately from the helper, so it reads the port
    from $ABA_HOME/installer/port.txt (sticky across helper restarts)."""
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    pf = tmp_path / "installer" / "port.txt"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text("9876")
    assert sp.helper_port() == 9876


def test_port_resolution_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    # No port.txt written; expect the documented 8765 default that
    # setup.command and the helper agree on.
    assert sp.helper_port() == 8765
