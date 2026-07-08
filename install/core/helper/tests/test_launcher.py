"""H5 — Launcher template renderer + install."""
import os
import stat
from pathlib import Path

import pytest

from aba_installer import launcher
from aba_installer.launcher import (
    LauncherContext, render, default_context,
    install_to_user_bin, user_install_path, discover_installed,
)


def test_template_file_exists():
    assert launcher.template_path().exists()


def test_render_substitutes_all_placeholders():
    ctx = LauncherContext(
        aba_home=Path("/tmp/ABA"),
        aba_runtime_dir=Path("/tmp/ABA/runtime"),
        aba_env=Path("/tmp/ABA/env"),
        aba_repo=Path("/tmp/ABA/repo"),
        aba_port=8000,
    )
    out = render(ctx)
    # No @@KEY@@ markers should remain
    assert "@@" not in out, f"unsubstituted markers in: {out[:300]}"
    # Substitutions landed
    assert '/tmp/ABA' in out
    assert '8000' in out


def test_render_preserves_bash_variables():
    """The template uses bash-isms like `$1` and `$HOME`. Substitution must
    NOT touch those — they're meant to be evaluated by bash at run time."""
    ctx = LauncherContext(
        aba_home=Path("/x"), aba_runtime_dir=Path("/x/runtime"),
        aba_env=Path("/x/env"), aba_repo=Path("/x/repo"),
    )
    out = render(ctx)
    assert "${1:-up}" in out         # bash default-expansion stays intact
    assert "$HOME/bin/aba" in out    # bash $HOME stays


def test_render_uses_custom_template_text():
    ctx = LauncherContext(
        aba_home=Path("/x"), aba_runtime_dir=Path("/y"),
        aba_env=Path("/z"), aba_repo=Path("/w"), aba_port=12345,
    )
    out = render(ctx, template_text="HOME=@@ABA_HOME@@ PORT=@@ABA_PORT@@")
    assert out == "HOME=/x PORT=12345"


def test_install_to_user_bin_writes_executable(tmp_aba_home):
    dest = install_to_user_bin()
    # Launcher lives under $ABA_HOME (self-contained), not ~/bin.
    assert dest == tmp_aba_home / "bin" / "aba"
    assert dest.exists()
    mode = stat.S_IMODE(os.stat(dest).st_mode)
    assert mode == 0o755, f"expected 0755, got {oct(mode)}"
    # Sanity: rendered shell starts with the shebang
    assert dest.read_text().startswith("#!/usr/bin/env bash")


def test_install_to_user_bin_replaces_existing(tmp_aba_home, monkeypatch):
    fake_home = tmp_aba_home / "homedir"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    p1 = install_to_user_bin()
    first = p1.read_text()
    # Overwrite with a different port
    ctx = LauncherContext(
        aba_home=tmp_aba_home, aba_runtime_dir=tmp_aba_home / "runtime",
        aba_env=tmp_aba_home / "env", aba_repo=tmp_aba_home / "repo",
        aba_port=9999,
    )
    p2 = install_to_user_bin(ctx)
    second = p2.read_text()
    assert "9999" in second
    assert second != first


def test_discover_installed_prefers_user_bin(tmp_aba_home, monkeypatch):
    fake_home = tmp_aba_home / "homedir"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    assert discover_installed() is None
    p = install_to_user_bin()
    assert discover_installed() == p


def test_default_context_resolves_under_aba_home(tmp_aba_home):
    ctx = default_context(port=8001)
    assert ctx.aba_home == tmp_aba_home
    assert ctx.aba_env == tmp_aba_home / "env"
    assert ctx.aba_repo == tmp_aba_home / "repo"
    assert ctx.aba_runtime_dir == tmp_aba_home / "runtime"
    assert ctx.aba_port == 8001


def test_default_context_reads_aba_port_env(tmp_aba_home, monkeypatch):
    """Headless installs bake the port from $ABA_PORT (setup.sh --port exports it),
    so a 2nd install on one host can pick a free port instead of colliding on :8000.
    Explicit arg wins; unset/garbage → DEFAULT_PORT."""
    monkeypatch.setenv("ABA_PORT", "8123")
    ctx = default_context()
    assert ctx.aba_port == 8123
    assert 'ABA_PORT="8123"' in render(ctx)          # baked into the launcher
    assert default_context(port=9000).aba_port == 9000   # explicit arg overrides env
    monkeypatch.delenv("ABA_PORT", raising=False)
    assert default_context().aba_port == launcher.DEFAULT_PORT   # unset → 8000
    monkeypatch.setenv("ABA_PORT", "notaport")
    assert default_context().aba_port == launcher.DEFAULT_PORT   # garbage → safe default


def test_rendered_launcher_has_known_subcommands():
    """The launcher's case statement must cover the actions we exposed in
    the Control UI: up / stop / status / logs / update / doctor / uninstall."""
    ctx = LauncherContext(
        aba_home=Path("/x"), aba_runtime_dir=Path("/x/runtime"),
        aba_env=Path("/x/env"), aba_repo=Path("/x/repo"),
    )
    out = render(ctx)
    for action in ("up)", "stop)", "status)", "logs)", "uninstall)"):
        assert action in out, f"launcher missing subcommand: {action}"
    # update / doctor / auth / hpc-config share one case → the headless CLI (browserless).
    assert "update|doctor|auth|hpc-config)" in out, "launcher missing the headless-CLI subcommand"
