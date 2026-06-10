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

    status_item   = rumps.MenuItem("⏳  Connecting…")
    start_item    = rumps.MenuItem("▶  Start")
    stop_item     = rumps.MenuItem("⏻  Stop")
    restart_item  = rumps.MenuItem("↻  Restart")
    open_item     = rumps.MenuItem("↗  Open ABA")
    updates_item  = rumps.MenuItem("⤓  Check for updates…")
    kickstart_item = rumps.MenuItem("Start helper…")
    quit_item     = rumps.MenuItem("Quit ABA Tray")

    items = {
        "status":    status_item,
        "start":     start_item,
        "stop":      stop_item,
        "restart":   restart_item,
        "open":      open_item,
        "updates":   updates_item,
        "kickstart": kickstart_item,
    }

    # Sender-name → handler. rumps passes the MenuItem as the sender of a
    # click; we dispatch by title prefix so menu.apply_status's title-edit
    # doesn't fight us.
    def on_click(sender):
        title = sender.title
        if title.startswith("▶"):
            res = actions.start(port=port)
        elif title.startswith("⏻"):
            res = actions.stop(port=port)
        elif title.startswith("↻"):
            res = actions.restart(port=port)
        elif title.startswith("↗"):
            res = actions.open_abc_browser(open_url=_open_url)
        elif title.startswith("⤓"):
            res = actions.check_updates(port=port, open_url=_open_url)
        elif "helper" in title.lower():
            res = actions.kickstart_helper()
        else:
            return
        # Surface success / failure in the macOS notification centre rather
        # than blocking the menu thread on a dialog.
        rumps.notification("ABA", "" if res.ok else "Error", res.message or "")

    def on_quit(_sender):
        rumps.quit_application()

    quit_item.set_callback(on_quit)

    # Build the menu in display order. None inserts a separator.
    app.menu = [
        status_item,
        None,
        start_item, stop_item, restart_item,
        None,
        open_item,
        None,
        updates_item, kickstart_item,
        None,
        quit_item,
    ]

    # Status row is informational; never clickable.
    status_item.set_callback(None)

    @rumps.timer(_POLL_SECONDS)
    def _poll(_sender):
        s = status_poll.fetch_status(port=port)
        menu.apply_status(items, s, callback=on_click)

    # Trigger an immediate first paint so the user doesn't sit on "Connecting…"
    _poll(None)

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
