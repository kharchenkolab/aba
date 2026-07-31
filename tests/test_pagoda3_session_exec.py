"""Guard: the pagoda3 lstar convert runs in the project SESSION env, never a bare
exec of a session interpreter path.

Regression (live 2026-07-21): the viewer convert did `subprocess.run([session_python,
'-m','lstar',...])` — but on a mount-adopted base the interpreter + lstar-sc live only
inside the session's activation/mount namespace, so the bare exec ran the CONTROLLER
venv and died 'No module named lstar'. Fix: build the command through the runtime
activation (project_env.exec_argv → ns-wrapped when the base is a squashfs mount)."""
from __future__ import annotations
import sys

import pytest

from content.bio.viewers.launchers import pagoda3

pytestmark = pytest.mark.platform


def test_pack_base_routes_through_session_activation(monkeypatch):
    from core.compute import base_env, project_env
    monkeypatch.setattr(base_env, "active", lambda lang: True)
    # the real exec_argv returns the ns-wrapped activation command; assert we USE it
    sentinel = ["bash", "-c", "ACT && exec python -m lstar convert x y"]
    seen = {}
    def fake_exec_argv(pid, lang, args):
        seen["pid"], seen["lang"], seen["args"] = pid, lang, list(args)
        return sentinel
    monkeypatch.setattr(project_env, "exec_argv", fake_exec_argv)

    argv = pagoda3._lstar_py_argv("prj_1", ["-m", "lstar", "convert", "s", "o"])
    assert argv is sentinel, "pack base must go through the session runtime activation"
    assert seen["lang"] == "python" and seen["args"][:2] == ["-m", "lstar"]
    # crucially, NOT a bare interpreter path
    assert not (argv and str(argv[0]).endswith("/python"))


def test_served_base_uses_local_interpreter(monkeypatch):
    from core.compute import base_env
    monkeypatch.setattr(base_env, "active", lambda lang: False)
    argv = pagoda3._lstar_py_argv(None, ["-c", "print(1)"])
    assert argv == [sys.executable, "-c", "print(1)"]


def test_resolution_error_falls_back_to_sys_executable(monkeypatch):
    from core.compute import base_env
    def boom(lang): raise RuntimeError("substrate offline")
    monkeypatch.setattr(base_env, "active", boom)
    argv = pagoda3._lstar_py_argv("prj_1", ["-m", "lstar", "x"])
    assert argv[0] == sys.executable
