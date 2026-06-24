"""env_refactor.md P4 — the agent-facing isolated-env control surface.

make_isolated_env (create + install with full version control) +
run_in_isolated_env (use the sandbox). The mechanism's conflict-resolution is
proven in test_isolated_env.py; here we pin the tool wiring + return shapes.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import content.bio  # noqa: E402,F401
import core.exec.materialize as mat  # noqa: E402
from content.bio.tools import make_isolated_env, run_in_isolated_env  # noqa: E402

pytestmark = pytest.mark.bio


@pytest.fixture
def iso_root(tmp_path, monkeypatch):
    monkeypatch.setattr(mat, "ENVS_DIR", tmp_path / "envs")
    return tmp_path


def test_make_requires_name():
    assert make_isolated_env({})["status"] == "error"


def test_run_requires_name_and_code():
    assert run_in_isolated_env({"name": "x"})["status"] == "error"


def test_make_env_only(iso_root):
    r = make_isolated_env({"name": "toolA"})
    assert r["status"] == "ok" and r["engine"] in ("uv", "venv")
    # can run in it immediately
    run = run_in_isolated_env({"name": "toolA", "code": "print('HELLO_ISO')"})
    assert run["status"] == "ok" and "HELLO_ISO" in run["stdout"]


def test_make_env_with_package_and_run(iso_root):
    r = make_isolated_env({"name": "toolB", "packages": ["six"], "verify_imports": ["six"]})
    if r["status"] != "ok" and any(s in str(r.get("error", "")) for s in
                                   ("Could not fetch", "Temporary failure",
                                    "Network is unreachable", "Failed to establish")):
        pytest.skip("no network for the isolated install")
    assert r["status"] == "ok", r
    assert r.get("verified") is True
    run = run_in_isolated_env({"name": "toolB", "code": "import six; print('SIX_OK')"})
    assert run["status"] == "ok" and "SIX_OK" in run["stdout"]


def test_run_in_missing_env_is_error(iso_root):
    r = run_in_isolated_env({"name": "ghost", "code": "print(1)"})
    assert r["status"] == "error" and "does not exist" in r["stderr"]


# ── solve-driven auto-isolation (UNSAT-against-base → isolate, not fail) ──────
def test_is_constraint_conflict():
    from content.bio.tools.discovery import _is_constraint_conflict
    assert _is_constraint_conflict("ERROR: ResolutionImpossible")
    assert _is_constraint_conflict("The conflict is caused by numpy==2.4.6 (from -c)")
    assert _is_constraint_conflict("these have conflicting dependencies")
    assert not _is_constraint_conflict("Connection timed out")
    assert not _is_constraint_conflict("No matching distribution found for typopkg")


def test_auto_isolate_success(monkeypatch):
    from core.exec import isolated_env as iso
    from content.bio.tools.discovery import _auto_isolate
    monkeypatch.setattr(iso, "create_env", lambda n, **k: {"name": n, "engine": "venv", "python": "/x"})
    monkeypatch.setattr(iso, "install_into",
                        lambda n, specs, **k: {"ok": True, "installed": list(specs), "verified": True})
    r = _auto_isolate("tflike", ["tflike==9"], {"import_name": "tflike"})
    assert r["status"] == "ready_isolated" and r["isolated_env"] == "cap-tflike"
    assert "run_in_isolated_env" in r["note"]


def test_auto_isolate_install_fails(monkeypatch):
    from core.exec import isolated_env as iso
    from content.bio.tools.discovery import _auto_isolate
    monkeypatch.setattr(iso, "create_env", lambda n, **k: {"name": n, "engine": "venv"})
    monkeypatch.setattr(iso, "install_into", lambda n, specs, **k: {"ok": False, "error": "boom"})
    assert _auto_isolate("x", ["x"], {})["status"] == "error"


def test_ensure_capability_auto_isolates_on_conflict(monkeypatch):
    """Integration: a pip capability that's UNSAT against the base routes to an
    isolated env instead of failing/corrupting."""
    import core.catalog as cat
    from core.exec import materialize as matz
    from core.exec import isolated_env as iso
    from content.bio.tools import discovery as d
    monkeypatch.setattr(cat, "resolve_capability", lambda name, *a, **k: {
        "name": name, "provisioning": {"pip": ["tflike==9"]},
        "import_name": "tflike", "scope": "project", "status": "published"})

    def boom(self, prov, scope="system", *, cancel_token=None, project_id=None):
        raise RuntimeError("ERROR: ResolutionImpossible. The conflict is caused by "
                           "numpy==2.4.6 (from -c constraints).")
    monkeypatch.setattr(matz.MaterializingExecutor, "materialize", boom)
    monkeypatch.setattr(iso, "create_env", lambda n, **k: {"name": n, "engine": "venv", "python": "/x"})
    monkeypatch.setattr(iso, "install_into",
                        lambda n, specs, **k: {"ok": True, "installed": list(specs), "verified": True})
    r = d.ensure_capability({"name": "tflike"})
    assert r["status"] == "ready_isolated", r
    assert r["isolated_env"] == "cap-tflike" and "run_in_isolated_env" in r["note"]


def test_ensure_capability_non_conflict_stays_error(monkeypatch):
    """A non-conflict materialize failure must NOT auto-isolate."""
    import core.catalog as cat
    from core.exec import materialize as matz
    from content.bio.tools import discovery as d
    monkeypatch.setattr(cat, "resolve_capability", lambda name, *a, **k: {
        "name": name, "provisioning": {"pip": ["x"]}, "import_name": "x",
        "scope": "project", "status": "published"})

    def boom(self, prov, scope="system", *, cancel_token=None, project_id=None):
        raise RuntimeError("network unreachable")
    monkeypatch.setattr(matz.MaterializingExecutor, "materialize", boom)
    r = d.ensure_capability({"name": "x"})
    assert r["status"] == "error" and "materialization failed" in r["note"]
