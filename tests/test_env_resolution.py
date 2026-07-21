"""Environment-selection campaign guards.

Fix order (each guard proven failing on the pre-fix code):
  1. env_layers() honesty — the R session layer must appear on the
     rlib-overlay topology. Pre-fix, an undefined name inside the branch
     raised NameError, the blanket except swallowed it, and the layer was
     silently dropped exactly where the layering question matters.
  2. set_active() validation — binding an env into a mismatched language
     slot (or a nonexistent env) is refused loudly; the setter used to
     write blindly.
"""
from __future__ import annotations

import os

import pytest

from core.compute import named_envs
from core.compute.errors import ComputeError
from core.exec import env_integrity

PID = "prj_envres"


# ── 1. env_layers() on the overlay topology ──────────────────────────────────

def _layers_with(monkeypatch, project_id, *, py_runtime, r_runtime,
                 py_prefix=None, r_prefix=None):
    """env_layers() against a faked session runtime; returns (out, ensure_calls)
    so tests can assert the branch under test actually ran (armed)."""
    from core.compute import base_env, project_env
    calls: list[str] = []

    def _ensure(pid, lang):
        calls.append(lang)
        if lang == "r":
            return {"runtime": dict(r_runtime), "prefix": r_prefix,
                    "materialized": True}
        return {"runtime": dict(py_runtime), "prefix": py_prefix,
                "materialized": True}

    monkeypatch.setattr(base_env, "active", lambda lang: True)
    monkeypatch.setattr(project_env, "ensure", _ensure)
    return env_integrity.env_layers(project_id), calls


def test_r_session_layer_survives_overlay_topology(monkeypatch, tmp_path):
    """The flagship guard: on the rlib-overlay topology (session R library
    riding a read-only base) the R session layer must be reported — path,
    mode, and the overlay's actual package content."""
    rlib = tmp_path / "rlib"
    (rlib / "overlaypkg").mkdir(parents=True)
    (rlib / "overlaypkg" / "DESCRIPTION").write_text("Package: overlaypkg\n")
    out, calls = _layers_with(monkeypatch, "prjL",
                              py_runtime={}, r_runtime={"rlib": str(rlib)})
    assert "r" in calls  # armed: the R session branch was actually reached
    sess = [l for l in out["r"]["layers"] if l["tier"] == "session"]
    assert len(sess) == 1, (
        "R session layer silently dropped on the rlib-overlay topology "
        f"(r layers: {out['r']['layers']})")
    assert sess[0]["mode"] == "rlib-overlay"
    assert sess[0]["path"] == str(rlib)
    assert [p["name"] for p in sess[0]["packages"]] == ["overlaypkg"]


def test_r_activation_only_yields_no_session_layer(monkeypatch, tmp_path):
    """Absent shape: no rlib, no direct prefix → no session layer, no crash."""
    out, calls = _layers_with(monkeypatch, "prjL", py_runtime={}, r_runtime={})
    assert "r" in calls
    assert [l for l in out["r"]["layers"] if l["tier"] == "session"] == []


def test_r_direct_exec_branch_unaffected(monkeypatch, tmp_path):
    """The other topology: a direct-exec R session reports its library path."""
    prefix = tmp_path / "renv"
    lib = prefix / "lib" / "R" / "library"
    lib.mkdir(parents=True)
    monkeypatch.setattr(
        env_integrity, "_r_packages_by_lib",
        lambda libs, rscript=None: {
            os.path.realpath(str(lib)): [{"name": "p", "version": "1"}]})
    out, _ = _layers_with(monkeypatch, "prjL", py_runtime={},
                          r_runtime={"direct_exec": True}, r_prefix=prefix)
    sess = [l for l in out["r"]["layers"] if l["tier"] == "session"]
    assert len(sess) == 1
    assert sess[0]["path"] == str(lib)
    assert sess[0]["packages"] == [{"name": "p", "version": "1"}]


def test_python_pylib_overlay_parity(monkeypatch, tmp_path):
    """The Python analog of the overlay branch keeps working (regression)."""
    pylib = tmp_path / "pylib"
    pylib.mkdir()
    out, _ = _layers_with(monkeypatch, "prjL",
                          py_runtime={"pylib": str(pylib)}, r_runtime={})
    sess = [l for l in out["python"]["layers"] if l["tier"] == "session"]
    assert len(sess) == 1
    assert sess[0]["path"] == str(pylib)


# ── 2. set_active() validation ───────────────────────────────────────────────

@pytest.fixture
def registry(monkeypatch, tmp_path):
    from core import config as _cfg
    monkeypatch.setattr(_cfg, "PROJECTS_DIR", tmp_path / "projects")
    named_envs._save(PID, {
        "envs": {
            "renv": {"env_id": "e:1", "language": "r", "packages": []},
            "pyenv": {"env_id": "e:2", "language": "python", "packages": []},
            "legacy": {"env_id": "e:3"},  # pre-language row → python
        },
        "active": {}, "default": {}})
    return PID


def test_set_active_rejects_language_mismatch(registry):
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "renv", "python")
    assert ei.value.code == "env.language_mismatch"
    assert named_envs.get_active(registry, "python") == "default"  # unchanged


def test_set_active_rejects_mismatch_via_default_lang(registry):
    """Absent shape: no lang argument defaults the slot — still validated."""
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "renv")
    assert ei.value.code == "env.language_mismatch"


def test_set_active_accepts_matching_language(registry):
    named_envs.set_active(registry, "renv", "r")
    assert named_envs.get_active(registry, "r") == "renv"
    named_envs.set_active(registry, "pyenv", "python")
    assert named_envs.get_active(registry, "python") == "pyenv"


def test_set_active_rejects_unknown_env(registry):
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "ghost", "python")
    assert ei.value.code == "unknown_env"
    assert named_envs.get_active(registry, "python") == "default"


def test_set_active_reserved_name_resets_pointer(registry):
    """'default' (and friends) are the reset path — no row exists, no
    validation applies, the pointer returns to the served stack."""
    named_envs.set_active(registry, "renv", "r")
    named_envs.set_active(registry, "default", "r")
    assert named_envs.get_active(registry, "r") == "default"


def test_set_active_legacy_row_language_defaults_python(registry):
    """A pre-language registry row counts as python: binds into the python
    slot, refuses the r slot."""
    named_envs.set_active(registry, "legacy", "python")
    assert named_envs.get_active(registry, "python") == "legacy"
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "legacy", "r")
    assert ei.value.code == "env.language_mismatch"
