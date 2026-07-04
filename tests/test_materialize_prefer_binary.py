"""Overlay pip installs must prefer prebuilt wheels (--prefer-binary).

On an old system toolchain (e.g. cluster GCC 4.8.5) source-building a package — or its
numpy build-dep — fails. If ANY version ships a wheel, pip should use it rather than
source-build the sdist-only latest. Regression: scikit-misc 0.5.2 is sdist-only while
0.5.1 has a manylinux wheel; without --prefer-binary pip picked 0.5.2 → built numpy →
"NumPy requires GCC >= 9.3" → dead end.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_mat_"))
_BE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BE not in sys.path:
    sys.path.insert(0, _BE)


class _FakeProc:
    returncode = 0
    stdout = ""
    stderr = ""


def test_overlay_pip_install_passes_prefer_binary(monkeypatch, tmp_path):
    from core.exec.materialize import MaterializingExecutor
    import core.exec.proc as proc
    captured = {}

    def _spy(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(proc, "run_cancellable", _spy)
    MaterializingExecutor()._pip_install(["scikit-misc"], prefix=tmp_path / "ov")
    cmd = captured["cmd"]
    assert "--prefer-binary" in cmd, f"overlay install must prefer wheels: {cmd}"
    # sanity: it's still an overlay install of the requested package
    assert "install" in cmd and "--prefix" in cmd and "scikit-misc" in cmd
