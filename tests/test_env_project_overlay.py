"""env_refactor.md P1 — per-project Python overlay (the blast-radius fix).

Closes the Python/R isolation asymmetry: Python now has a per-project overlay
(like R's r_libs/prj_<id>), so one project's on-demand install can't pollute
another. These pin: path layout, scope→tier routing, the preamble append order,
and — the whole point — containment.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.exec.materialize as m  # noqa: E402
from core.exec.env_integrity import verify_python_imports  # noqa: E402

pytestmark = pytest.mark.platform


def test_project_pylib_paths_layout():
    assert m.project_pylib_paths(None) == []
    assert m.project_pylib_paths("") == []
    paths = m.project_pylib_paths("prjZ")
    assert paths, "a project id must yield at least one site-packages dir"
    assert all(str(m.PROJECT_PYLIB_ROOT) in str(p) and "prjZ" in str(p) for p in paths)


def test_routing_by_scope(monkeypatch):
    """project/user-scoped → the project's overlay; installation/system → shared."""
    from core.exec.base import Provisioning
    captured: dict = {}

    def fake_pip(self, packages, *, cancel_token=None, prefix=None):
        captured["prefix"] = prefix

    monkeypatch.setattr(m.MaterializingExecutor, "_pip_install", fake_pip)
    ex = m.MaterializingExecutor()

    ex.materialize(Provisioning(pip=["six"]), scope="project:prjQ", project_id="prjQ")
    assert captured["prefix"] == m.project_pylib_dir("prjQ")

    captured.clear()
    ex.materialize(Provisioning(pip=["six"]), scope="installation", project_id="prjQ")
    assert captured.get("prefix") is None   # shared overlay

    captured.clear()
    ex.materialize(Provisioning(pip=["six"]), scope="project:prjQ", project_id=None)
    assert captured.get("prefix") is None   # no project → shared


def test_project_overlay_containment(tmp_path, monkeypatch):
    """The whole point: a package in project A's overlay is visible to A but NOT
    to project B."""
    monkeypatch.setattr(m, "PROJECT_PYLIB_ROOT", tmp_path / "pylib_proj")
    a_sp = m.project_pylib_paths("prjA")[0]
    a_sp.mkdir(parents=True)
    (a_sp / "abaonlyA.py").write_text("MARK = 'A'\n")

    okA, _ = verify_python_imports(["abaonlyA"],
                                   extra_paths=[str(p) for p in m.project_pylib_paths("prjA")])
    assert okA, "project A must see its own overlay package"

    okB, _ = verify_python_imports(["abaonlyA"],
                                   extra_paths=[str(p) for p in m.project_pylib_paths("prjB")])
    assert not okB, "containment: project A's package must be invisible to project B"


def test_preamble_appends_project_overlay(monkeypatch):
    """run_python's sys.path = base → shared overlay → THIS project's overlay."""
    from core.exec.kernels import jupyter
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: "prjPRE")
    code = jupyter._setup_code("/tmp")
    assert str(m.project_pylib_dir("prjPRE")) in code, "preamble must append the project overlay"
    assert str(m.PYLIB_DIR) in code, "shared overlay must still be present"
    # ordering: shared overlay appears before the project overlay
    assert code.index(str(m.PYLIB_DIR)) < code.index(str(m.project_pylib_dir("prjPRE")))


def test_preamble_sets_pip_guard(monkeypatch):
    """Ad-hoc-install containment: the kernel preamble points a bare `pip
    install` at the project overlay (not the shared base) via PIP_PREFIX."""
    from core.exec.kernels import jupyter
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: "prjPIP")
    code = jupyter._setup_code("/tmp")
    assert "PIP_PREFIX" in code
    assert str(m.project_pylib_dir("prjPIP")) in code  # ad-hoc pip → project overlay
    # and it must NOT point at the base .venv (the corruption path)
    import sys as _sys
    assert str(Path(_sys.executable).parent.parent) not in code.split("PIP_PREFIX")[1].split("\n")[0]


def test_preamble_execs_with_pip_guard_and_imports_project_pkg(tmp_path, monkeypatch):
    """Integration: run the EXACT preamble string a kernel execs — confirm a
    project-overlay package imports AND PIP_PREFIX is set to the project
    overlay (so a reflexive `pip install` lands contained, not in the base)."""
    import subprocess
    from core.exec.kernels import jupyter
    from core import projects
    monkeypatch.setattr(m, "PROJECT_PYLIB_ROOT", tmp_path / "pylib_proj")
    monkeypatch.setattr(projects, "current", lambda: "prjLIVE")
    sp = m.project_pylib_paths("prjLIVE")[0]
    sp.mkdir(parents=True)
    (sp / "abalive.py").write_text("OK = 1\n")
    preamble = jupyter._setup_code(str(tmp_path))   # the literal string a kernel runs
    script = (preamble + "\nimport abalive\nimport os as _o\n"
              "print('PIP_PREFIX=' + _o.environ.get('PIP_PREFIX',''))\n"
              "print('LIVE_IMPORT_OK')\n")
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=60)
    assert "LIVE_IMPORT_OK" in (proc.stdout or ""), \
        f"preamble failed to put the project overlay on sys.path:\n{(proc.stderr or '')[-600:]}"
    assert f"PIP_PREFIX={m.project_pylib_dir('prjLIVE')}" in proc.stdout, \
        f"ad-hoc pip not pointed at the project overlay:\n{proc.stdout}"


def test_r_libpaths_puts_project_lib_first():
    """R symmetry to the Python PIP_PREFIX guard: install.packages() lands in the
    project lib (not the base) because libpaths_expr PREPENDS the project lib to
    .libPaths(), so .libPaths()[1] is the project lib."""
    from core.exec.r import libpaths_expr, project_r_lib
    proj = str(project_r_lib("prjR"))
    expr = libpaths_expr("prjR")
    assert expr.startswith(f".libPaths(c({proj!r}"), "project lib must be first in .libPaths()"
    assert ".libPaths()" in expr, "the base libs must remain after the project lib"
    assert libpaths_expr(None) == "", "no project → no .libPaths override"
