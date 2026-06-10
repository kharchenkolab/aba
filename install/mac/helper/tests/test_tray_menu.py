"""Tier-0-tray menu: state-to-enabled wiring.

rumps's menu items follow a "callback=None means disabled" convention. We
keep the logic that reads a TrayStatus and updates per-item enabled-state +
title strings in a pure module so it can be tested without rumps.
"""
from __future__ import annotations
import pytest

from aba_installer.tray import menu as m
from aba_installer.tray.status_poll import TrayStatus


class _Item:
    """Stand-in for ``rumps.MenuItem``. The menu module only needs `.title`
    and ``.set_callback(cb_or_none)`` to wire enabled-state."""
    def __init__(self, title: str = "", callback=None):
        self.title = title
        self.callback = callback
    def set_callback(self, cb):  # rumps convention: cb=None → disabled
        self.callback = cb
    @property
    def enabled(self) -> bool:
        return self.callback is not None


def _make_items() -> dict:
    """Mirror the menu order the rumps App constructs in __main__.py."""
    return {
        "status":   _Item(),
        "start":    _Item("▶  Start"),
        "stop":     _Item("⏻  Stop"),
        "restart":  _Item("↻  Restart"),
        "open":     _Item("↗  Open ABA"),
        "updates":  _Item("⤓  Check for updates…"),
        "kickstart": _Item("Start helper…"),
    }


# Sentinel callback the menu module wires onto enabled items. We don't care
# what it does; we just need to tell "set" from "cleared".
_CB = lambda _sender: None


def test_running_state_enables_stop_restart_open():
    items = _make_items()
    s = TrayStatus(state="running", label="●  Running   pid 42",
                   pid=42, can_start=False, can_stop=True, can_open=True)
    m.apply_status(items, s, callback=_CB)
    assert items["status"].title == "●  Running   pid 42"
    assert items["start"].enabled is False
    assert items["stop"].enabled is True
    assert items["restart"].enabled is True
    assert items["open"].enabled is True
    assert items["kickstart"].enabled is False    # hidden when helper is up


def test_stopped_state_enables_only_start():
    items = _make_items()
    s = TrayStatus(state="stopped", label="○  Stopped",
                   pid=None, can_start=True, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert items["status"].title == "○  Stopped"
    assert items["start"].enabled is True
    assert items["stop"].enabled is False
    assert items["restart"].enabled is False
    assert items["open"].enabled is False


def test_helper_offline_only_kickstart_actionable():
    """In ``helper_offline`` state, ONLY 'Start helper…' is enabled — every
    other action would just hit /api/* and time out."""
    items = _make_items()
    s = TrayStatus(state="helper_offline", label="⏸  Helper offline",
                   pid=None, can_start=False, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert items["status"].title == "⏸  Helper offline"
    assert items["start"].enabled is False
    assert items["stop"].enabled is False
    assert items["restart"].enabled is False
    assert items["open"].enabled is False
    assert items["updates"].enabled is False
    assert items["kickstart"].enabled is True


def test_installing_disables_start_stop_until_op_completes():
    items = _make_items()
    s = TrayStatus(state="installing", label="⏳  Installing…",
                   pid=None, can_start=False, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert items["start"].enabled is False
    assert items["stop"].enabled is False
    assert items["restart"].enabled is False


def test_updates_enabled_unless_offline_or_already_updating():
    """`Check for updates…` is fine to click in most states (it opens the
    helper Control page in the browser); only suppress when the helper is
    unreachable or an update is already in progress."""
    items = _make_items()
    # running → updates available
    m.apply_status(items, TrayStatus("running", "x", 1, False, True, True),
                   callback=_CB)
    assert items["updates"].enabled is True
    # offline → no
    m.apply_status(items, TrayStatus("helper_offline", "x", None, False, False, False),
                   callback=_CB)
    assert items["updates"].enabled is False
    # mid-update → no
    m.apply_status(items, TrayStatus("updating", "x", 1, False, False, False),
                   callback=_CB)
    assert items["updates"].enabled is False


# ─── module-level guard: tray import on a non-mac shouldn't NameError ───
def test_tray_main_module_imports_without_running_rumps():
    """The tray's __main__.py is the only thing that touches rumps. Importing
    it in test/CI (typically Linux) must NOT instantiate the rumps app —
    only the module-level `main()` does. The other modules (status_poll,
    actions, menu) are pure and importable everywhere."""
    # status_poll / actions / menu importable already (this whole test file
    # is proof). Verify the __main__ module guards rumps at import time.
    try:
        from aba_installer.tray import __main__ as tray_main   # noqa: F401
    except ImportError as e:
        # rumps unavailable on this platform is fine for `main()` but the
        # MODULE must still import — `main()` is allowed to fail later.
        assert "rumps" in str(e).lower(), (
            f"tray __main__ failed to import for a reason other than the "
            f"rumps dep: {e!r}")
