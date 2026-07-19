"""core.exec.verify — real load-verification + GPU verify-at-use probes.

Split out of test_env_integrity (W3.4 served-base retirement). The corruption
that bit us was reported "ready" because the check was PathFinder.find_spec
(presence), not a real import. These tests pin the present-but-unloadable case
(ABI mismatch / partial install) that find_spec misses but a real import catches,
plus the torch GPU verify-at-use signal.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.exec.verify import verify_python_imports  # noqa: E402

pytestmark = pytest.mark.platform


# ── Python import verification ───────────────────────────────────────────────
def test_verify_python_imports_good():
    ok, detail = verify_python_imports(["os", "sys", "json"], extra_paths=[])
    assert ok and detail == ""


def test_verify_python_imports_missing():
    ok, detail = verify_python_imports(["no_such_module_xyz_123"], extra_paths=[])
    assert not ok
    assert "No module named" in detail or "ModuleNotFoundError" in detail


def test_verify_python_imports_present_but_unloadable(tmp_path):
    """The tensorflow case: a package that EXISTS (find_spec passes) but raises
    on import. find_spec says ready; a real import does not."""
    pkg = tmp_path / "abadummy"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "raise ImportError('numpy.core.multiarray failed to import')\n")
    # find_spec WOULD green-light it (the old, wrong check)
    from importlib.machinery import PathFinder
    assert PathFinder.find_spec("abadummy", [str(tmp_path)]) is not None
    # the real check catches it
    ok, detail = verify_python_imports(["abadummy"], extra_paths=[str(tmp_path)])
    assert not ok
    assert "multiarray" in detail


def test_verify_python_imports_empty_is_ok():
    assert verify_python_imports([], extra_paths=[]) == (True, "")


def test_verify_python_imports_appends_overlay_not_prepend(tmp_path):
    """Overlay paths must be APPENDED (base wins), matching the run_python
    preamble — verify by shadowing a stdlib name in the overlay and confirming
    the base copy still wins."""
    (tmp_path / "json.py").write_text("raise RuntimeError('overlay json should not win')\n")
    ok, _ = verify_python_imports(["json"], extra_paths=[str(tmp_path)])
    assert ok  # base json wins because the extra path is appended, not prepended


# ── GPU verify-at-use (torch) ────────────────────────────────────────────────
def test_gpu_capability_ok_maps_torch_state(monkeypatch):
    """The GPU verify-at-use signal: torch sees a GPU → True; torch present but no
    usable GPU (CPU-only build — the scVI-on-CPU incident) → False; torch absent →
    None (not a torch GPU job)."""
    import types
    import core.exec.verify as ver
    fake = types.SimpleNamespace(
        __version__="2.9.0",
        version=types.SimpleNamespace(cuda="12.4"),
        cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake)
    ok, _ = ver.gpu_capability_ok()
    assert ok is True
    # CPU-only build: version.cuda is None and cuda.is_available() is False → the incident
    fake.version.cuda = None
    fake.cuda.is_available = lambda: False
    ok, detail = ver.gpu_capability_ok()
    assert ok is False and "is_available()=False" in detail
    # torch not importable → not judged
    monkeypatch.setitem(sys.modules, "torch", None)
    ok, _ = ver.gpu_capability_ok()
    assert ok is None


def test_torch_cuda_build_reports_build(monkeypatch):
    """torch_cuda_build reflects the BUILD (version.cuda), node-independently — None for
    a CPU-only build or absent torch (the login-node signal for whether a GPU JOB could
    use the GPU)."""
    import types
    import core.exec.verify as ver
    monkeypatch.setitem(sys.modules, "torch",
                        types.SimpleNamespace(version=types.SimpleNamespace(cuda="12.4")))
    assert ver.torch_cuda_build() == "12.4"
    monkeypatch.setitem(sys.modules, "torch",
                        types.SimpleNamespace(version=types.SimpleNamespace(cuda=None)))
    assert ver.torch_cuda_build() is None
    monkeypatch.setitem(sys.modules, "torch", None)   # not importable
    assert ver.torch_cuda_build() is None
