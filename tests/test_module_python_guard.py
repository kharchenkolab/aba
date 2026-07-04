"""Guard: python-toolchain Lmod modules are never recorded or job-loaded.

Such a module (e.g. 'scanpy/1.4.4-foss-2018b-python-3.6.6', 'Python/3.6.6') carries
its own Python; `module load`ed into a background job running the conda-env python it
shadows the env's site-packages (the prj_6d986f40 numpy incident). record_project_module
refuses them; project_modules self-heals any recorded before the guard existed.
"""
from __future__ import annotations
import os, sys, json, tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import core.exec.modules as M  # noqa: E402


def test_is_python_module_classification():
    assert M._is_python_module("scanpy/1.4.4-foss-2018b-python-3.6.6")
    assert M._is_python_module("Python/3.6.6")
    assert M._is_python_module("SciPy-bundle/2020.03-foss-2020a-Python-3.8.2")
    # binaries / non-python-toolchain modules must NOT be flagged
    assert not M._is_python_module("cellranger/7.0.0")
    assert not M._is_python_module("BWA/0.7.17-foss-2018b")
    assert not M._is_python_module("STAR/2.7.9a")
    assert not M._is_python_module("biopython/1.79")   # name contains 'python' but not a toolchain module


def test_record_and_read_filter(monkeypatch, tmp_path):
    f = tmp_path / "modules.json"
    monkeypatch.setattr(M, "_project_modules_file", lambda pid: f)
    monkeypatch.setattr(M, "modules_active", lambda: True)

    # record refuses a python-toolchain module, accepts a CLI tool
    M.record_project_module("p", "scanpy/1.4.4-foss-2018b-python-3.6.6")
    M.record_project_module("p", "cellranger/7.0.0")
    assert M.project_modules("p") == ["cellranger/7.0.0"], M.project_modules("p")

    # self-heal: a file poisoned BEFORE the guard (written directly) is filtered on read
    f.write_text(json.dumps(["cellranger/7.0.0", "scanpy/1.4.4-foss-2018b-python-3.6.6"]))
    assert M.project_modules("p") == ["cellranger/7.0.0"], "did not self-heal a pre-existing python module"


def test_pythonpath_excluded_from_delta_vars():
    """A module's PYTHONPATH/PYTHONHOME must never be captured into a delta — that's
    what shadows the conda env's site-packages in a job. Binary/library paths only."""
    assert "PYTHONPATH" not in M._PATH_VARS
    assert "PYTHONHOME" not in M._PATH_VARS and "PYTHONHOME" not in M._SCALAR_VARS


if __name__ == "__main__":
    test_is_python_module_classification()
    print("PASS test_is_python_module_classification")
