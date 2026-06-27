"""Tier-0-tray LaunchAgent: auto-starts the ABA.app at user login.

Mirrors install/core/helper/tests/test_launchagent.py's structure (the
helper LaunchAgent's tests). Same idempotency + plist-shape expectations,
distinct label, distinct target.
"""
from __future__ import annotations
import plistlib
from pathlib import Path

import pytest

from aba_installer import tray_launchagent as tla


# ─── template + rendering ──────────────────────────────────────────────
def test_template_exists_and_has_known_substitution_markers():
    """The renderer expects @@APP_LAUNCHER@@ and @@HELPER_DIR@@ in the
    template — same convention as launchagent.py."""
    text = tla.template_path().read_text()
    assert "@@APP_LAUNCHER@@" in text
    assert "@@HELPER_DIR@@" in text


def test_render_substitutes_all_markers():
    ctx = tla.AgentContext(
        app_launcher=Path("/u/Applications/ABA.app/Contents/MacOS/aba-tray"),
        helper_dir=Path("/u/.aba/installer"),
        aba_home=Path("/u/.aba"),
    )
    rendered = tla.render(ctx)
    assert "@@" not in rendered, (
        "all markers must be substituted; leftover @@…@@ means a missing "
        "field in AgentContext.substitutions()")
    assert "/u/Applications/ABA.app/Contents/MacOS/aba-tray" in rendered
    assert "/u/.aba/installer" in rendered


def test_render_produces_valid_plist():
    ctx = tla.AgentContext(
        app_launcher=Path("/u/Applications/ABA.app/Contents/MacOS/aba-tray"),
        helper_dir=Path("/u/.aba/installer"),
        aba_home=Path("/u/.aba"),
    )
    pl = plistlib.loads(tla.render(ctx).encode())
    assert pl["Label"] == "com.kharchenkolab.aba.tray"
    # Distinct label from the helper's plist — same machine runs both.
    assert pl["Label"] != "com.kharchenkolab.aba.helper"
    # ProgramArguments points at the bundle's MacOS executable.
    assert pl["ProgramArguments"][0].endswith("/aba-tray")
    # RunAtLoad: yes (start on login). KeepAlive: should NOT be true — if
    # the user quits the tray from the menu, the launchd-restart loop
    # would un-quit it and the experience reads as broken. § 3c.7 design.
    assert pl["RunAtLoad"] is True
    assert pl.get("KeepAlive") is not True


def test_default_context_targets_user_applications():
    """User-local Applications folder — no admin needed (Tier-0b doesn't
    have admin)."""
    ctx = tla.default_context()
    assert "Applications" in str(ctx.app_launcher), (
        f"expected the launcher under Applications/, got {ctx.app_launcher}")
    assert str(ctx.app_launcher).endswith("/aba-tray")


# ─── label uniqueness + uninstall path ─────────────────────────────────
def test_label_distinct_from_helper_label():
    """If these ever collide, launchctl unload of one wipes the other.
    Guard with an explicit identity test."""
    from aba_installer import launchagent
    assert tla.LABEL != launchagent.LABEL
    assert tla.LABEL == "com.kharchenkolab.aba.tray"


def test_plist_destination_in_launch_agents_dir():
    """User-level LaunchAgents live in ~/Library/LaunchAgents/; no admin."""
    dest = tla.plist_destination()
    assert str(dest).endswith(".plist")
    assert "LaunchAgents" in str(dest)


# ─── launchctl wrappers: mock the subprocess boundary ──────────────────
def test_install_launch_agent_writes_plist_and_loads(tmp_path, monkeypatch):
    """Cover the install path without actually shelling to launchctl —
    intercept _launchctl, assert it was asked to load the right plist."""
    plist = tmp_path / "tray.plist"
    monkeypatch.setattr(tla, "plist_destination", lambda: plist)
    ctx = tla.AgentContext(
        app_launcher=tmp_path / "ABA.app" / "Contents" / "MacOS" / "aba-tray",
        helper_dir=tmp_path / "installer",
        aba_home=tmp_path,
    )
    calls: list = []
    monkeypatch.setattr(tla, "_launchctl",
                        lambda *args: (calls.append(args), (0, "", ""))[1])
    monkeypatch.setattr(tla, "is_loaded", lambda: False)
    tla.install_launch_agent(ctx)
    assert plist.exists()
    # We called launchctl load -w with the destination plist
    assert any("load" in a for a in calls), f"no launchctl load: {calls}"
    pl = plistlib.loads(plist.read_text().encode())
    assert pl["Label"] == "com.kharchenkolab.aba.tray"


def test_uninstall_launch_agent_unloads_and_removes(tmp_path, monkeypatch):
    plist = tmp_path / "tray.plist"
    plist.write_text("<?xml version='1.0'?><plist><dict/></plist>")
    monkeypatch.setattr(tla, "plist_destination", lambda: plist)
    monkeypatch.setattr(tla, "is_loaded", lambda: True)
    calls: list = []
    monkeypatch.setattr(tla, "_launchctl",
                        lambda *args: (calls.append(args), (0, "", ""))[1])
    removed = tla.uninstall_launch_agent()
    assert removed is True
    assert not plist.exists()
    assert any("unload" in a for a in calls), f"no launchctl unload: {calls}"
