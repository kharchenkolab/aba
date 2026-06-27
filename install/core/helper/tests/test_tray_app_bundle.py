"""Tier-0-tray ABA.app bundle template: structure + Info.plist + launcher.

The bundle is a static template copied verbatim into ``~/Applications/``
by setup.command (slice 6); the shell launcher inside execs the helper
venv's python, so the bundle has no per-install codegen. Tests verify the
template is well-formed before setup.command tries to copy it.
"""
from __future__ import annotations
import os
import plistlib
import stat
import subprocess
from pathlib import Path

import pytest


# ─── locate the in-repo template ───────────────────────────────────────
# tests/ → helper/ → mac/ → install/ → repo root
ROOT = Path(__file__).resolve().parents[4]
APP_TEMPLATE = ROOT / "install" / "mac" / "tray" / "ABA.app"


def test_app_bundle_layout():
    """Apple's bundle spec — Contents/{Info.plist, MacOS, Resources}."""
    assert APP_TEMPLATE.is_dir(), f"missing template at {APP_TEMPLATE}"
    assert (APP_TEMPLATE / "Contents").is_dir()
    assert (APP_TEMPLATE / "Contents" / "Info.plist").is_file()
    assert (APP_TEMPLATE / "Contents" / "MacOS").is_dir()
    assert (APP_TEMPLATE / "Contents" / "Resources").is_dir()


def test_info_plist_is_valid_and_complete():
    """Parse Info.plist; check load-bearing keys for a tray-only LSUIElement
    app: identifier, name, executable, LSUIElement=true."""
    pl = plistlib.loads((APP_TEMPLATE / "Contents" / "Info.plist").read_bytes())
    # Required Apple keys
    assert pl["CFBundleIdentifier"] == "com.kharchenkolab.aba.tray"
    assert pl["CFBundleName"] == "ABA"
    assert pl["CFBundlePackageType"] == "APPL"
    assert pl["CFBundleExecutable"] == "aba-tray"
    # Tray-specific: no Dock icon (LSUIElement) — purely menu-bar.
    assert pl["LSUIElement"] is True, (
        "LSUIElement must be true for a menu-bar-only app; otherwise the "
        "user gets an unwanted Dock entry every time they relaunch.")
    # Version present so macOS doesn't complain in Console.
    assert "CFBundleVersion" in pl and "CFBundleShortVersionString" in pl


def test_launcher_script_executable_and_well_formed():
    """The MacOS/aba-tray shell is what macOS spawns when the user double-
    clicks ABA.app. It must be (a) executable, (b) syntactically valid sh,
    (c) exec into the helper venv's python."""
    launcher = APP_TEMPLATE / "Contents" / "MacOS" / "aba-tray"
    assert launcher.is_file(), f"missing launcher at {launcher}"
    # Executable bit
    mode = launcher.stat().st_mode
    assert mode & stat.S_IXUSR, (
        f"launcher must be executable; mode is {oct(mode)}. The build "
        f"step or git's permission tracking lost the +x bit.")
    # Syntactically valid /bin/sh
    proc = subprocess.run(["sh", "-n", str(launcher)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, (
        f"launcher syntax error: {proc.stderr}")
    # Content commits to the helper venv python + the tray module. The
    # launcher composes the path from shell vars (so we look for the
    # discriminating fragments, not the materialized string).
    body = launcher.read_text()
    assert "installer/venv/bin/python" in body, (
        "launcher should exec the helper venv's python, not the system one")
    assert ".aba" in body, (
        "launcher should reference the ABA home dir (or $ABA_HOME) so the "
        "venv path resolves correctly")
    assert "aba_installer.tray" in body, (
        "launcher should run the tray entrypoint via -m")
    assert "exec " in body, (
        "use exec to replace the shell process — otherwise we leave a "
        "stranded /bin/sh in the process tree forever")
    # Bundle-root anchor: __main__.py can't find the icon from sys.argv[0]
    # when run via python -m (argv[0] = installed package's __main__.py,
    # NOT this launcher). The launcher exports ABA_TRAY_BUNDLE pointing at
    # Contents/ so the tray module can locate Resources/TrayIconTemplate.png.
    assert "ABA_TRAY_BUNDLE" in body, (
        "launcher must export ABA_TRAY_BUNDLE for the tray module to find "
        "its icons. Without this the menu-bar item falls back to a text "
        "title — likely invisible-looking to the user.")
    assert 'export' in body and 'ABA_TRAY_BUNDLE' in body, (
        "ABA_TRAY_BUNDLE must be exported, not just assigned")


def test_launcher_starts_with_shebang():
    """First line is the shebang — required by macOS for a launchable
    Contents/MacOS/<name> shell script."""
    launcher = APP_TEMPLATE / "Contents" / "MacOS" / "aba-tray"
    first = launcher.read_text().splitlines()[0]
    assert first.startswith("#!/"), (
        f"first line of launcher must be a shebang, got: {first!r}")
    # /bin/sh is on every Mac; don't depend on Homebrew bash.
    assert "/bin/sh" in first, f"prefer /bin/sh, got: {first!r}"
