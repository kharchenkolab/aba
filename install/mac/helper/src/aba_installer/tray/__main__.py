"""Tray entrypoint — the rumps app.

Module-level imports stay rumps-free so `python -m aba_installer.tray`
imports cleanly on any platform; rumps is imported lazily inside ``main()``
so the package's other test surfaces (status_poll, actions, menu) remain
testable on CI Linux. Calling ``main()`` on a non-darwin host raises
ImportError; that's expected.
"""
from __future__ import annotations
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from aba_installer.tray import actions, menu, status_poll


_POLL_SECONDS = 3.0


def _open_log_path(p: Path) -> None:
    """OS-default opener for a .log file. macOS only."""
    subprocess.Popen(["open", str(p)])


def _open_url(url: str) -> None:
    webbrowser.open(url)


def _log_dir() -> Path:
    home = Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))
    return home / "logs"


def main() -> int:
    """Boot the menu-bar app. rumps is imported here so non-darwin imports
    of this module don't fail at collection time."""
    import rumps   # noqa: PLC0415 — guarded to keep the rest of the package testable

    port = status_poll.helper_port()

    # Find the Template glyph inside the running .app bundle. The launcher
    # (Contents/MacOS/aba-tray) exports ABA_TRAY_BUNDLE pointing at the
    # Contents/ dir — we look for Resources/ underneath. Falling back to
    # walking up from this module's __file__ does NOT work because pip
    # installed the package into the venv's site-packages, which has no
    # relation to the .app bundle on disk.
    bundle_contents = os.environ.get("ABA_TRAY_BUNDLE")
    icon_path = Path(bundle_contents) / "Resources" / "TrayIconTemplate.png" \
        if bundle_contents else None
    # Source-tree dev: ABA_TRAY_BUNDLE unset, but the repo template lives
    # at a discoverable relative path next to the helper package.
    if icon_path is None or not icon_path.exists():
        repo_template = (Path(__file__).resolve().parents[5]
                         / "install" / "mac" / "tray" / "ABA.app"
                         / "Contents" / "Resources"
                         / "TrayIconTemplate.png") \
            if len(Path(__file__).resolve().parents) >= 6 else None
        if repo_template and repo_template.exists():
            icon_path = repo_template
    has_icon = icon_path is not None and icon_path.exists()
    app = rumps.App("ABA", title=None if has_icon else "ABA",
                    icon=str(icon_path) if has_icon else None,
                    template=True, quit_button=None)

    status_item    = rumps.MenuItem("⏳  Connecting…")
    start_item     = rumps.MenuItem("▶  Start")
    stop_item      = rumps.MenuItem("⏻  Stop")
    restart_item   = rumps.MenuItem("↻  Restart")
    updates_item   = rumps.MenuItem("⤓  Check for updates…")
    update_now_item = rumps.MenuItem("⚙  Update now (no UI)…")
    model_item     = rumps.MenuItem("Model")            # submenu container
    kickstart_item = rumps.MenuItem("Start helper…")
    quit_item      = rumps.MenuItem("Quit ABA Tray")

    items = {
        "status":     status_item,
        "start":      start_item,
        "stop":       stop_item,
        "restart":    restart_item,
        "updates":    updates_item,
        "update_now": update_now_item,
        "model":      model_item,
        "kickstart":  kickstart_item,
    }

    # Sender-name → handler. The status row's title varies ('●  Open ABA'
    # when running, '○  ABA Stopped' otherwise) but apply_status only sets
    # its callback when running, so a title match on '●  Open' is enough
    # to identify the Open action.
    def on_click(sender):
        title = sender.title
        if title.startswith("▶"):
            res = actions.start(port=port)
        elif title.startswith("⏻"):
            res = actions.stop(port=port)
        elif title.startswith("↻"):
            res = actions.restart(port=port)
        elif title.startswith("●") and "Open" in title:
            res = actions.open_abc_browser(open_url=_open_url)
        elif title.startswith("⤓"):
            res = actions.check_updates(port=port, open_url=_open_url)
        elif title.startswith("⚙") and "Update" in title:
            # Inline update — bypasses the helper entirely. Notification
            # callback bound below carries title + body through rumps.
            res = actions.update_inline(
                open_path=lambda p: subprocess.Popen(["open", str(p)]),
                notify=lambda t, sub, body: rumps.notification(t, sub, body),
            )
        elif "helper" in title.lower():
            res = actions.kickstart_helper()
        else:
            return
        rumps.notification("ABA", "" if res.ok else "Error", res.message or "")

    # Model submenu — one callback per model id (created via factory so
    # the id is captured by closure, not lost in the rumps sender chain).
    def make_model_callback(model_id: str):
        def _click(_sender):
            res = actions.set_model(model_id=model_id, port=port)
            if res.ok and res.applied_on_next_turn:
                # Hot-switch contract: backend reads the new model at the
                # start of the next turn. No restart needed.
                rumps.notification("ABA", "Switched model",
                                   f"{model_id} — takes effect on your next "
                                   f"message.")
            elif not res.ok:
                rumps.notification("ABA", "Error", res.message or "")
        return _click

    def on_quit(_sender):
        rumps.quit_application()

    quit_item.set_callback(on_quit)

    # Build the menu in display order. None inserts a separator.
    # Note: NO separate Open row — the status row at top does double duty.
    app.menu = [
        status_item,
        None,
        start_item, stop_item, restart_item,
        None,
        model_item, updates_item, update_now_item, kickstart_item,
        None,
        quit_item,
    ]

    @rumps.timer(_POLL_SECONDS)
    def _poll(_sender):
        s = status_poll.fetch_status(port=port)
        menu.apply_status(items, s, callback=on_click)
        # Refresh the Model submenu — current selection may have changed
        # via the browser Control page; available list could have widened.
        try:
            ms = status_poll.fetch_model_state(port=port)
            menu.apply_model_submenu(model_item, current=ms.current,
                                     available=ms.available,
                                     callback_factory=make_model_callback)
        except Exception:  # noqa: BLE001 — keep the poll resilient
            pass

    # Trigger an immediate first paint so the user doesn't sit on "Connecting…"
    _poll(None)

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
