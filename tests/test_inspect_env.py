"""env_refactor.md §8.3 — inspect_env, the agent's read layer for env trouble.

Unlike inspect_package (learns an importable package's API), inspect_env tells
you whether/why something loads — the troubleshooting signal. Tested both
languages.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import content.bio  # noqa: E402,F401
from content.bio.tools import inspect_env  # noqa: E402

pytestmark = pytest.mark.bio


def test_inspect_env_python_package():
    r = inspect_env({"name": "numpy"})
    assert r["status"] == "ok" and r["language"] == "python"
    assert r["loads"] is True and r["version"]
    assert r["tier"] == "base"


def test_inspect_env_python_missing():
    r = inspect_env({"name": "no_such_pkg_zzz"})
    assert r["loads"] is False and r["error"]


def test_inspect_env_overview():
    r = inspect_env({})   # no name → tier overview
    assert r["status"] == "ok" and r["scope"] == "overview"
    # W3.5 weft-only: the overview reports the weft session, not pip overlays.
    assert "session" in r["tiers"] and "python" in r["tiers"]


def test_inspect_env_present_but_broken(tmp_path):
    """The tensorflow case: a package that EXISTS but won't import is reported
    loads=False with the error (not a silent pass). python_package_status probes
    the interpreter + any extra_paths and runs a REAL import."""
    from core.exec.env_integrity import python_package_status
    (tmp_path / "abatfsim").mkdir()
    (tmp_path / "abatfsim" / "__init__.py").write_text(
        "raise ImportError('numpy.core.multiarray failed to import')\n")
    r = python_package_status("abatfsim", extra_paths=[str(tmp_path)])
    assert r["loads"] is False and "multiarray" in (r["error"] or "")


def test_inspect_env_r_real():
    # weft-only: R introspection needs a declared R base pack + realizable session.
    from core.compute import base_env
    if not base_env.active("r"):
        pytest.skip("no R base pack declared in this deployment")
    r = inspect_env({"name": "Matrix", "language": "r"})
    assert r["language"] == "r" and r["loads"] is True and r["version"]
    r2 = inspect_env({"name": "NoSuchRPkgXyz", "language": "r"})
    assert r2["loads"] is False
