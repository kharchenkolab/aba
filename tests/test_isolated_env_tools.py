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
