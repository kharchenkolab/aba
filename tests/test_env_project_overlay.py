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


def test_routing_by_project_id(monkeypatch):
    """Overlay routing is by PROJECT_ID presence, not scope: ALL runtime installs go
    to the project's own overlay when a project is active (materialize.py — the shared
    overlay is folded into the immutable base); only a no-project context falls back to
    the legacy shared prefix (None)."""
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
    assert captured.get("prefix") == m.project_pylib_dir("prjQ")   # project_id wins over scope

    captured.clear()
    ex.materialize(Provisioning(pip=["six"]), scope="project:prjQ", project_id=None)
    assert captured.get("prefix") is None   # no project → legacy shared prefix


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


