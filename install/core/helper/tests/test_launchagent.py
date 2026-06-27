"""H6 — LaunchAgent plist rendering + install (mocked launchctl)."""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from aba_installer import launchagent
from aba_installer.launchagent import (
    AgentContext, default_context, render,
    install_launch_agent, uninstall_launch_agent, plist_destination, LABEL,
)


def test_template_exists():
    assert launchagent.template_path().exists()


def test_render_substitutes_all_placeholders():
    ctx = AgentContext(
        venv_python=Path("/x/bin/python"),
        helper_dir=Path("/x"),
        aba_home=Path("/aba"),
    )
    out = render(ctx)
    assert "@@" not in out, f"unsubstituted markers in: {out[:300]}"
    assert "/x/bin/python" in out
    assert "/aba" in out
    assert LABEL in out


def test_render_produces_valid_xml():
    """plist is XML — must at least parse as such."""
    import xml.etree.ElementTree as ET
    ctx = AgentContext(
        venv_python=Path("/x/bin/python"),
        helper_dir=Path("/x"),
        aba_home=Path("/aba"),
    )
    out = render(ctx)
    root = ET.fromstring(out)
    assert root.tag == "plist"
    # Label is in there
    text = ET.tostring(root, encoding="unicode")
    assert LABEL in text


def test_default_context_uses_helper_venv_when_present(tmp_aba_home):
    venv_py = tmp_aba_home / "installer" / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    ctx = default_context()
    assert ctx.venv_python == venv_py


def test_default_context_falls_back_to_sys_python_when_venv_missing(tmp_aba_home):
    import sys
    ctx = default_context()
    assert ctx.venv_python == Path(sys.executable)


def test_install_writes_plist_to_user_launchagents(tmp_aba_home, monkeypatch):
    fake_home = tmp_aba_home / "homedir"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    # Stub out launchctl so the test doesn't touch the system
    calls = []
    def fake_launchctl(*args):
        calls.append(args)
        return (0, "", "")
    monkeypatch.setattr(launchagent, "_launchctl", fake_launchctl)
    monkeypatch.setattr(launchagent, "is_loaded", lambda: False)

    ctx = AgentContext(
        venv_python=tmp_aba_home / "venv" / "bin" / "python",
        helper_dir=tmp_aba_home / "installer",
        aba_home=tmp_aba_home,
    )
    dest = install_launch_agent(ctx)
    assert dest == fake_home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert dest.exists()
    # launchctl was told to load -w
    load_calls = [c for c in calls if c[0] == "load"]
    assert load_calls, f"expected launchctl load call; got {calls}"
    assert "-w" in load_calls[0]


def test_install_reloads_when_already_loaded(tmp_aba_home, monkeypatch):
    fake_home = tmp_aba_home / "homedir"; fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    calls = []
    monkeypatch.setattr(launchagent, "_launchctl",
                        lambda *a: (calls.append(a), (0, "", ""))[1])
    monkeypatch.setattr(launchagent, "is_loaded", lambda: True)
    install_launch_agent(AgentContext(
        venv_python=Path("/x"), helper_dir=tmp_aba_home, aba_home=tmp_aba_home,
    ))
    ops = [c[0] for c in calls]
    assert "unload" in ops and "load" in ops, f"expected unload+load; got {ops}"


def test_uninstall_removes_plist(tmp_aba_home, monkeypatch):
    fake_home = tmp_aba_home / "homedir"; fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(launchagent, "_launchctl", lambda *a: (0, "", ""))
    monkeypatch.setattr(launchagent, "is_loaded", lambda: True)
    install_launch_agent(AgentContext(
        venv_python=Path("/x"), helper_dir=tmp_aba_home, aba_home=tmp_aba_home,
    ))
    assert plist_destination().exists()
    removed = uninstall_launch_agent()
    assert removed is True
    assert not plist_destination().exists()


def test_uninstall_idempotent_when_absent(tmp_aba_home, monkeypatch):
    fake_home = tmp_aba_home / "homedir"; fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(launchagent, "_launchctl", lambda *a: (0, "", ""))
    monkeypatch.setattr(launchagent, "is_loaded", lambda: False)
    removed = uninstall_launch_agent()
    assert removed is False
