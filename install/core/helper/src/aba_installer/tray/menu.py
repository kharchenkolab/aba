"""Tray menu — state-to-label + state-to-enabled wiring + Model submenu.

Pure logic. Takes rumps-shaped menu items (anything with ``.title``,
``.set_callback``, ``.state``, ``.clear()`` / ``.add()`` for submenus —
rumps's MenuItem fits, our test stubs fit) plus a TrayStatus snapshot,
and updates titles + enabled-state.

Design choice captured in the labels:

- When ABA is running, the STATUS ROW is the Open ABA affordance
  ('●  Open ABA', clickable). There's no separate Open menu item.
- When ABA is stopped, the status row reads '○  ABA Stopped' and is
  inert; Start is the only action.
- rumps menu titles do not support per-character colour, so 'green
  circle' is the strongest text glyph we have: ● (filled) vs ○ (empty).

The ``status`` field on rumps.MenuItem is the checkmark toggle for the
Model submenu's currently-selected entry.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, Optional, Sequence

from aba_installer.tray.status_poll import TrayStatus


# Map TrayStatus.state → menu label (the row a user reads first).
# Running is the only state where the row doubles as an action.
def status_label(status: TrayStatus) -> str:
    state = status.state
    if state == "running":
        return "●  Open ABA"
    if state == "stopped":
        return "○  ABA Stopped"
    if state == "installing":
        return "⏳  Installing…"
    if state == "updating":
        return "⏳  Updating…"
    if state == "no_credentials":
        return "○  Sign in to ABA…"
    if state == "not_installed":
        return "○  Setup in progress…"
    if state == "helper_offline":
        return "⏸  Helper offline"
    # Unknown — surface verbatim rather than swallow.
    return status.label or f"○  {state}"


def apply_status(items: Dict[str, Any], status: TrayStatus,
                 *, callback: Callable) -> None:
    """Update menu items for the given status.

    ``items`` must contain keys: status, start, stop, restart, updates,
    model, kickstart. ``callback`` is the single click handler — rumps
    passes the MenuItem as the sender, and the handler in __main__.py
    dispatches by sender title.
    """
    items["status"].title = status_label(status)

    def _set(item_key: str, enabled: bool) -> None:
        items[item_key].set_callback(callback if enabled else None)

    # Status row is clickable ONLY when running — then it's Open ABA.
    _set("status", status.state == "running")

    _set("start",   status.can_start)
    _set("stop",    status.can_stop)
    _set("restart", status.can_stop)         # mirror Stop's gate
    # Check for updates → smart cascade (probes '/', kickstarts helper if
    # broken, falls back to inline). It works in helper_offline because
    # the inline path doesn't need the helper. Disabled only mid-update
    # so a second click can't spawn a parallel run.
    _set("updates", status.state != "updating")
    # Model submenu: persistence-only operation. Gate the parent on helper
    # reachability so a click doesn't hit a dead /api/auth/model.
    _set("model",   status.state != "helper_offline")
    # 'Start helper…' is the recovery affordance when the LaunchAgent is
    # down — only relevant in helper_offline.
    _set("kickstart", status.state == "helper_offline")


def apply_model_submenu(submenu: Any, *, current: Optional[str],
                        available: Sequence[dict],
                        callback_factory: Callable[[str], Callable]) -> None:
    """Rebuild the Model submenu's children from /api/auth/model state.

    Each child gets a callback bound to its model id (via the factory).
    The currently-selected model gets a checkmark (rumps state=1). Pass
    current=None to render every entry unchecked — that's the right
    behaviour when the helper hasn't surfaced a current model yet
    (config.env mid-write, brand-new install, etc.).
    """
    # rumps.MenuItem.clear() crashes with AttributeError if the parent has
    # never had children added — the underlying NSMenu only gets created
    # on the first .add(). Guard on len() so the first poll cycle still
    # populates instead of dying silently in _poll's except-Exception.
    if len(submenu) > 0:
        try:
            submenu.clear()
        except (AttributeError, TypeError):
            # Belt-and-suspenders for older rumps releases that don't
            # implement clear() on a never-materialised submenu.
            pass
    # Construct fresh children. rumps's MenuItem has the same API our
    # _Item stub exposes — title in ctor, set_callback, state attribute.
    try:
        import rumps   # type: ignore
        _MenuItem = rumps.MenuItem
    except Exception:    # noqa: BLE001 — tests pass in a stub class
        _MenuItem = None
    for opt in available:
        if _MenuItem is not None:
            child = _MenuItem(opt["label"])
        else:
            # Test path: synthesise an item with the same surface area.
            child = _make_stub_item(opt["label"])
        child.set_callback(callback_factory(opt["id"]))
        child.state = 1 if (current and opt["id"] == current) else 0
        submenu.add(child)


def _make_stub_item(title: str):
    """Used only when rumps isn't importable (test environment).
    Mirror the test's _Item shape so tests can assert against it
    even when apply_model_submenu is called outside their own fixture."""
    class _StubItem:
        def __init__(self, t):
            self.title = t
            self.callback = None
            self.state = 0
        def set_callback(self, cb):
            self.callback = cb
    return _StubItem(title)
