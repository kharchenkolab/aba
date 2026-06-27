"""Tier-0-tray installer step: copy the bundle to ~/Applications, clear the
Gatekeeper quarantine, install the tray LaunchAgent. setup.command shells
into this module to do the work; we test it directly so the install path
isn't load-bearing on a real Mac in CI."""
from __future__ import annotations
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aba_installer import tray_install as ti


# ─── locate the in-repo bundle template (same as test_tray_app_bundle) ──
ROOT = Path(__file__).resolve().parents[4]
APP_TEMPLATE = ROOT / "install" / "mac" / "tray" / "ABA.app"


# ─── (1) Copy the bundle into ~/Applications/ ──────────────────────────
def test_install_app_copies_bundle_into_user_applications(tmp_path, monkeypatch):
    """Bundle template is copied verbatim into ~/Applications/ABA.app. Same
    Info.plist, same launcher, +x bit preserved on aba-tray."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    # Avoid touching real /usr/bin/xattr + launchctl from the test
    monkeypatch.setattr(ti, "_clear_quarantine", lambda p: None)
    monkeypatch.setattr(ti, "_install_launch_agent", lambda: None)

    dest = ti.install_tray()
    assert dest == tmp_path / "Applications" / "ABA.app"
    assert dest.is_dir()
    # Info.plist round-trips through plistlib
    pl = plistlib.loads((dest / "Contents" / "Info.plist").read_bytes())
    assert pl["CFBundleIdentifier"] == "com.kharchenkolab.aba.tray"
    # The launcher is executable
    launcher = dest / "Contents" / "MacOS" / "aba-tray"
    assert os.access(launcher, os.X_OK), (
        f"+x bit lost during install copy: {oct(launcher.stat().st_mode)}")


def test_install_app_replaces_an_older_bundle(tmp_path, monkeypatch):
    """Re-running setup.command should replace the existing bundle, not
    fail with FileExistsError or leave a half-old / half-new tree."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(ti, "_clear_quarantine", lambda p: None)
    monkeypatch.setattr(ti, "_install_launch_agent", lambda: None)
    apps = tmp_path / "Applications"
    apps.mkdir()
    # Plant a stale bundle whose Info.plist is gibberish
    stale = apps / "ABA.app"
    (stale / "Contents").mkdir(parents=True)
    (stale / "Contents" / "Info.plist").write_text("STALE")
    (stale / "Contents" / "stale_marker").write_text("nuke me")

    ti.install_tray()
    # Stale marker gone, real plist back
    assert not (stale / "Contents" / "stale_marker").exists()
    pl = plistlib.loads((stale / "Contents" / "Info.plist").read_bytes())
    assert pl["CFBundleIdentifier"] == "com.kharchenkolab.aba.tray"


# ─── (2) Quarantine clear ───────────────────────────────────────────────
def test_quarantine_xattr_cleared_after_copy(tmp_path, monkeypatch):
    """After copy, _clear_quarantine runs `xattr -dr com.apple.quarantine`
    on the destination — without this, the user gets a "downloaded from
    Internet, are you sure?" dialog the first time launchd kickstarts the
    tray."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(ti, "_install_launch_agent", lambda: None)
    seen = []
    def fake_run(argv, **k):
        seen.append(argv)
        class _P: returncode = 0; stdout = ""; stderr = ""
        return _P()
    monkeypatch.setattr(ti, "_run", fake_run)

    ti.install_tray()
    # We invoked xattr -d / -dr with com.apple.quarantine on the dest
    xattr_calls = [a for a in seen if a[0] == "xattr"]
    assert xattr_calls, f"no xattr call recorded: {seen}"
    flat = " ".join(xattr_calls[0])
    assert "com.apple.quarantine" in flat
    assert "ABA.app" in flat


def test_quarantine_clear_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    """If xattr fails (Gatekeeper attribute not present, or xattr binary
    missing on a weird machine) the install must still complete. The
    failure will surface as a one-time Gatekeeper dialog when the user
    first launches ABA.app — not as a hard install failure."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(ti, "_install_launch_agent", lambda: None)
    def failing_run(argv, **k):
        class _P: returncode = 1; stdout = ""; stderr = "xattr: not found"
        return _P()
    monkeypatch.setattr(ti, "_run", failing_run)
    # Should NOT raise.
    ti.install_tray()


# ─── (3) Tray LaunchAgent install is delegated ─────────────────────────
def test_install_tray_calls_install_launch_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(ti, "_clear_quarantine", lambda p: None)
    called = []
    monkeypatch.setattr(ti, "_install_launch_agent",
                        lambda: called.append(1))
    ti.install_tray()
    assert called == [1], "install_tray must end by registering the LaunchAgent"


# ─── (4) gating ────────────────────────────────────────────────────────
def test_main_runs_install_when_flag_set(tmp_path, monkeypatch):
    """The CLI entrypoint that setup.command shells into checks the env
    flag. Without it set, it skips the whole install step (v1 opt-in)."""
    monkeypatch.setenv("ABA_INSTALL_TRAY", "1")
    called = []
    monkeypatch.setattr(ti, "install_tray", lambda: called.append(1) or tmp_path)
    rc = ti.main()
    assert rc == 0
    assert called == [1]


def test_main_skips_when_flag_unset(monkeypatch):
    monkeypatch.delenv("ABA_INSTALL_TRAY", raising=False)
    called = []
    monkeypatch.setattr(ti, "install_tray", lambda: called.append(1) or None)
    rc = ti.main()
    assert rc == 0
    assert called == [], (
        "without ABA_INSTALL_TRAY=1, main() must be a no-op so setup.command "
        "doesn't accidentally install the tray on every old install during "
        "the v1 rollout window")
