"""Tier-0-tray menu: state-to-label wiring + status row as Open affordance
+ Model submenu construction.

rumps's menu items follow a "callback=None means disabled" convention. We
keep the logic that reads a TrayStatus and updates per-item enabled-state +
title strings in a pure module so it can be tested without rumps.

Design notes captured here:
- When ABA is running, the STATUS ROW *is* the Open ABA affordance
  (●  Open ABA), clickable. There's no separate Open row.
- When ABA is stopped, the status row reads ○  ABA Stopped and is inert.
- Model selection lives in a submenu that mirrors what /api/auth/model
  returns; the currently-selected model has rumps's state=1 (checkmark).
"""
from __future__ import annotations
import pytest

from aba_installer.tray import menu as m
from aba_installer.tray.status_poll import TrayStatus


class _Item:
    """Stand-in for ``rumps.MenuItem``. The menu module only needs:
      - .title (str, writable)
      - .set_callback(cb or None)         → enabled/disabled gate
      - .state (int)                       → 1 = checkmark, 0 = none
      - .clear() / .add(item)              → submenu mutation
    """
    def __init__(self, title: str = "", callback=None):
        self.title = title
        self.callback = callback
        self.state = 0
        self._children: list = []
    def set_callback(self, cb):
        self.callback = cb
    def clear(self):
        self._children.clear()
    def add(self, child):
        self._children.append(child)
    @property
    def enabled(self) -> bool:
        return self.callback is not None


def _make_items() -> dict:
    """Mirror the menu order the rumps App constructs in __main__.py
    AFTER the redesign (no separate `open` item; new `model` submenu)."""
    return {
        "status":    _Item(),
        "start":     _Item("▶  Start"),
        "stop":      _Item("⏻  Stop"),
        "restart":   _Item("↻  Restart"),
        "updates":   _Item("⤓  Check for updates…"),
        "model":     _Item("Model"),         # submenu container
        "kickstart": _Item("Start helper…"),
    }


# Sentinel callback the menu module wires onto enabled items.
_CB = lambda _sender: None


# ─── status row labels + clickability ──────────────────────────────────
def test_running_state_status_row_is_clickable_open_affordance():
    """●  Open ABA — clickable. The status row becomes the open button."""
    items = _make_items()
    s = TrayStatus(state="running", label="(unused)", pid=42,
                   can_start=False, can_stop=True, can_open=True)
    m.apply_status(items, s, callback=_CB)
    assert items["status"].title == "●  Open ABA", (
        f"unexpected label: {items['status'].title!r}")
    assert items["status"].enabled is True, (
        "status row must be clickable when running")


def test_stopped_state_status_row_is_inert():
    """○  ABA Stopped — not clickable; Start is the only action."""
    items = _make_items()
    s = TrayStatus(state="stopped", label="(unused)", pid=None,
                   can_start=True, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert items["status"].title == "○  ABA Stopped"
    assert items["status"].enabled is False
    assert items["start"].enabled is True


def test_helper_offline_status_row_shows_specific_label():
    items = _make_items()
    s = TrayStatus(state="helper_offline", label="(unused)", pid=None,
                   can_start=False, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert items["status"].title == "⏸  Helper offline"
    assert items["status"].enabled is False
    assert items["kickstart"].enabled is True


def test_installing_state_status_label():
    items = _make_items()
    s = TrayStatus(state="installing", label="(unused)", pid=None,
                   can_start=False, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert "Installing" in items["status"].title
    assert items["status"].enabled is False


def test_updating_state_status_label_and_inert_status_row():
    items = _make_items()
    s = TrayStatus(state="updating", label="(unused)", pid=99,
                   can_start=False, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert "Updating" in items["status"].title
    # During an update we don't want the user to open the SPA into a half-
    # restarted backend — keep the row inert.
    assert items["status"].enabled is False


def test_no_credentials_status_label():
    items = _make_items()
    s = TrayStatus(state="no_credentials", label="(unused)", pid=None,
                   can_start=False, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert "Sign in" in items["status"].title or "sign in" in items["status"].title


# ─── start/stop/restart wiring unchanged from earlier ──────────────────
def test_running_disables_start_enables_stop_restart():
    items = _make_items()
    s = TrayStatus(state="running", label="", pid=42,
                   can_start=False, can_stop=True, can_open=True)
    m.apply_status(items, s, callback=_CB)
    assert items["start"].enabled is False
    assert items["stop"].enabled is True
    assert items["restart"].enabled is True


def test_stopped_enables_only_start():
    items = _make_items()
    s = TrayStatus(state="stopped", label="", pid=None,
                   can_start=True, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert items["start"].enabled is True
    assert items["stop"].enabled is False
    assert items["restart"].enabled is False


# ─── model submenu construction ────────────────────────────────────────
_AVAILABLE = [
    {"id": "claude-haiku-4-5",  "label": "Haiku 4.5 (fast, cheap)",
     "note": "Best for simple lookups."},
    {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6 (balanced)",
     "note": "Most real work."},
    {"id": "claude-opus-4-7",   "label": "Opus 4.7 (highest quality)",
     "note": "Complex multi-step."},
]


def test_apply_model_submenu_rebuilds_children_with_one_checkmark():
    """Three model items appear under the Model submenu; the currently-
    selected one has rumps state=1 (checkmark)."""
    sub = _Item("Model")
    sub.add(_Item("stale"))   # something from a previous poll cycle
    factory_calls = []
    def factory(model_id):
        factory_calls.append(model_id)
        return _CB
    m.apply_model_submenu(sub, current="claude-sonnet-4-6",
                          available=_AVAILABLE, callback_factory=factory)
    # Old contents gone, three new items in order
    titles = [c.title for c in sub._children]
    assert titles == [opt["label"] for opt in _AVAILABLE], (
        f"unexpected titles: {titles}")
    # Exactly one item has state=1, and it's the sonnet entry
    checked = [c for c in sub._children if c.state == 1]
    assert len(checked) == 1
    assert checked[0].title.startswith("Sonnet")
    # Factory got called once per model so callbacks are bound to specific ids
    assert factory_calls == [opt["id"] for opt in _AVAILABLE]


def test_apply_model_submenu_no_check_when_current_is_none():
    """If the helper says no current model (e.g. config.env still seeding),
    don't paint a checkmark on a random one. All three render unchecked."""
    sub = _Item("Model")
    m.apply_model_submenu(sub, current=None,
                          available=_AVAILABLE,
                          callback_factory=lambda _id: _CB)
    assert all(c.state == 0 for c in sub._children)


def test_apply_model_submenu_disabled_when_helper_offline():
    """The Model submenu's parent item is disabled when the helper isn't
    reachable — a click would just hit /api/auth/model and time out."""
    items = _make_items()
    s = TrayStatus(state="helper_offline", label="", pid=None,
                   can_start=False, can_stop=False, can_open=False)
    m.apply_status(items, s, callback=_CB)
    assert items["model"].enabled is False


def test_apply_model_submenu_enabled_when_running_or_stopped():
    items = _make_items()
    for st in ("running", "stopped", "installing", "updating"):
        m.apply_status(items, TrayStatus(st, "", None, False, False, False),
                       callback=_CB)
        # Setting the model is just persistence; helper is up in all these
        # states. Enable the parent so the submenu opens.
        assert items["model"].enabled is True, (
            f"Model menu should be enabled in state {st}")


# ─── import guard, unchanged ────────────────────────────────────────────
def test_tray_main_module_imports_without_running_rumps():
    try:
        from aba_installer.tray import __main__ as tray_main   # noqa: F401
    except ImportError as e:
        assert "rumps" in str(e).lower(), (
            f"tray __main__ failed to import for a reason other than the "
            f"rumps dep: {e!r}")
