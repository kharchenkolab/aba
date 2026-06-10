"""Tray menu — state-to-enabled wiring.

Pure logic. Takes a dict of rumps-shaped menu items (anything with a
``.title`` attribute and ``.set_callback(cb)`` method — rumps's MenuItem
fits, our test stubs fit) plus a TrayStatus snapshot, and updates each
item's enabled-state and title.

The "set callback to None = disabled" pattern is rumps's convention; we
follow it so this module can drive a real rumps.App or a unit-test stub
identically.
"""
from __future__ import annotations
from typing import Any, Callable, Dict

from aba_installer.tray.status_poll import TrayStatus


def apply_status(items: Dict[str, Any], status: TrayStatus,
                 *, callback: Callable) -> None:
    """Update menu items for the given status.

    ``items`` must contain the keys: status, start, stop, restart, open,
    updates, kickstart. ``callback`` is what rumps calls when a menu item
    is clicked — same function for every action; the action it dispatches
    is decided by the item the user clicked, which rumps passes as the
    sender argument."""
    items["status"].title = status.label

    def _set(item_key: str, enabled: bool) -> None:
        items[item_key].set_callback(callback if enabled else None)

    _set("start",   status.can_start)
    _set("stop",    status.can_stop)
    # Restart is enabled whenever Stop is — the action sequences stop+start
    # internally (see actions.restart).
    _set("restart", status.can_stop)
    _set("open",    status.can_open)

    # `Check for updates…` opens the helper's browser Control page, which
    # itself can disambiguate state. Disable it only when there's no point:
    # the helper is unreachable, or an update is already in flight.
    can_update = status.state not in ("helper_offline", "updating")
    _set("updates", can_update)

    # 'Start helper…' is the recovery affordance when the LaunchAgent is
    # down. It's pointless otherwise; surface it only in helper_offline.
    _set("kickstart", status.state == "helper_offline")
