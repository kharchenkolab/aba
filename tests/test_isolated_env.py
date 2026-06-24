"""env_refactor.md P4 — isolated environments (the escape hatch + agent sandbox).

A requirement that's UNSAT against the install-wide base (a conflicting numpy,
tensorflow) gets a FULL independent env the agent owns — isolation contains the
mess, the base is never touched. Engine: uv if present, else stdlib venv.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.exec.materialize as mat  # noqa: E402
import core.exec.isolated_env as iso  # noqa: E402

pytestmark = pytest.mark.platform


@pytest.fixture
def iso_root(tmp_path, monkeypatch):
    monkeypatch.setattr(mat, "ENVS_DIR", tmp_path / "envs")
    return tmp_path


def _skip_if_no_network(res):
    err = (res.get("error") or "")
    if any(s in err for s in ("Could not fetch", "Temporary failure", "Network is unreachable",
                              "Failed to establish", "No matching distribution")):
        pytest.skip("no network for the isolated install")


def test_create_run_list_remove(iso_root):
    info = iso.create_env("e1")
    assert info["created"] is True and Path(info["python"]).exists()
    assert info["engine"] in ("uv", "venv")
    # idempotent
    again = iso.create_env("e1")
    assert again["created"] is False
    r = iso.run_in("e1", "import sys; print('PYOK', sys.version_info[0])")
    assert r["ok"] and "PYOK 3" in r["stdout"]
    assert "e1" in iso.list_envs()
    assert iso.remove_env("e1") and "e1" not in iso.list_envs()


def test_install_and_verify(iso_root):
    res = iso.install_into("e2", ["six"], verify_imports=["six"], timeout_s=300)
    _skip_if_no_network(res)
    assert res["ok"], res["error"]
    assert res["verified"] is True
    r = iso.run_in("e2", "import six; print('SIX=' + six.__version__)")
    assert r["ok"] and "SIX=" in r["stdout"]


def test_isolated_resolves_numpy_conflict(iso_root):
    """The escape hatch: a numpy that CONFLICTS with the base (which pins 2.x)
    lives in the isolated env — proving the agent can resolve a conflict the
    shared base can't, without touching the base."""
    import numpy as _basenp
    base_ver = _basenp.__version__
    assert base_ver.startswith("2."), f"test assumes base numpy 2.x, got {base_ver}"
    res = iso.install_into("nptest", ["numpy==1.26.4"], timeout_s=600)
    _skip_if_no_network(res)
    assert res["ok"], res["error"]
    r = iso.run_in("nptest", "import numpy; print('ISO_NP=' + numpy.__version__)")
    assert "ISO_NP=1.26.4" in r["stdout"], r
    # the base in THIS process is untouched — the conflict is contained
    assert _basenp.__version__ == base_ver


def test_run_in_missing_env_is_graceful(iso_root):
    r = iso.run_in("nope", "print(1)")
    assert r["ok"] is False and "does not exist" in r["stderr"]
