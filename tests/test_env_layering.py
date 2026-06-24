"""env_refactor.md §11.4 — the layering flip: project overlay prepended (project
wins, R-parity), numpy/ABI core pinned via the anchor, shared overlay folded into
the base (all runtime installs route to the per-project overlay)."""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform


def test_abi_anchor_pins_numpy_only(tmp_path, monkeypatch):
    from core.exec import env_integrity as ei
    monkeypatch.setattr(ei, "base_constraints_path", lambda: tmp_path / "base.txt")
    monkeypatch.setattr(ei, "abi_anchor_path", lambda: tmp_path / "anchor.txt")
    ei.ensure_base_constraints(force=True)          # real freeze of this interpreter
    anchor = ei.abi_anchor_constraints(force=True)
    assert anchor is not None
    lines = [l for l in anchor.read_text().splitlines() if l.strip()]
    assert any(l.lower().startswith("numpy==") for l in lines), lines
    # ONLY the anchor packages — not the full freeze (so overrides of others pass)
    assert all(l.split("==")[0].strip().lower() in ("numpy",) for l in lines)


def test_materialize_routes_all_installs_to_project_overlay(tmp_path, monkeypatch):
    """§11.4: even installation/system scope now lands in the PROJECT overlay —
    nothing writes the shared overlay anymore."""
    from core.exec import materialize as mat
    monkeypatch.setattr(mat, "ENVS_DIR", tmp_path / "envs")
    ex = mat.MaterializingExecutor()
    seen = {}
    monkeypatch.setattr(ex, "_pip_install",
                        lambda pkgs, **k: seen.update(prefix=k.get("prefix"), pkgs=list(pkgs)))
    for scope in ("installation", "system", "project"):
        seen.clear()
        ex.materialize(mat.Provisioning(pip=["somepkg"]), scope=scope, project_id="prjX")
        assert seen["prefix"] == mat.project_pylib_dir("prjX"), scope   # never PYLIB_DIR


def test_pip_install_uses_anchor_for_project_overlay(tmp_path, monkeypatch):
    """A project-overlay install constrains with the ABI anchor, not the full
    base freeze (so the project can override ordinary versions)."""
    from core.exec import materialize as mat
    from core.exec import env_integrity as ei
    monkeypatch.setattr(mat, "ENVS_DIR", tmp_path / "envs")
    monkeypatch.setattr(ei, "abi_anchor_path", lambda: tmp_path / "anchor.txt")
    monkeypatch.setattr(ei, "base_constraints_path", lambda: tmp_path / "base.txt")
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        class R:  # noqa: E306
            returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr("core.exec.proc.run_cancellable", fake_run)
    ex = mat.MaterializingExecutor()
    ex._pip_install(["somepkg"], prefix=mat.project_pylib_dir("prjX"))
    cmd = " ".join(str(c) for c in captured["cmd"])
    assert "anchor.txt" in cmd and "base.txt" not in cmd
