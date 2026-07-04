"""Guard: ensure_capability must NOT record a cluster module for a pip LIBRARY.

The prj_6d986f40 incident: `resolve("scanpy")` matched the Lmod module
`scanpy/1.4.4-foss-2018b-python-3.6.6` (a python PACKAGE exposed as a module,
carrying its own Python 3.6.6). It got recorded into the project's modules.json,
and every background Slurm job then `module load`ed it — shadowing the conda env's
numpy 2.4.6 with 1.17.3 → every background job died on import. Cluster modules are
for CLI/binary tools only; pip libraries live in the conda env.
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="aba_capgate_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "g.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "art")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402,F401  (registers the pack + tool modules)

import core.exec.modules as _mods       # noqa: E402
import core.catalog as _cat             # noqa: E402
from content.bio.tools.discovery import ensure_capability  # noqa: E402


def _patch_modules(monkeypatch, resolved):
    recorded = []
    monkeypatch.setattr(_mods, "modules_active", lambda: True)
    monkeypatch.setattr(_mods, "resolve", lambda name: resolved)
    monkeypatch.setattr(_mods, "record_project_module", lambda pid, mod: recorded.append(mod))
    monkeypatch.setattr(_mods, "kernel_env_snippet", lambda mod: "")   # no live-kernel side effect
    return recorded


def test_pip_library_does_not_record_cluster_module(monkeypatch):
    """A pip library (archetype=library / provisioning.pip), already importable,
    must NOT record a cluster module even though one matches by name."""
    recorded = _patch_modules(monkeypatch, resolved="scanpy/1.4.4-foss-2018b-python-3.6.6")
    monkeypatch.setattr(_cat, "resolve_capability", lambda n: {
        "name": "scanpy", "archetype": "library",
        "provisioning": {"pip": ["scanpy"]}, "import_name": "sys"})  # 'sys' importable → early ready
    res = ensure_capability({"name": "scanpy"})
    assert recorded == [], f"pip library wrongly recorded a cluster module: {recorded}"
    assert res.get("status") == "ready", res


def test_cli_tool_records_cluster_module(monkeypatch):
    """An uncatalogued CLI/binary tool provided by a cluster module DOES record it
    (the legitimate case — cellranger etc.)."""
    recorded = _patch_modules(monkeypatch, resolved="cellranger/7.0.0")
    monkeypatch.setattr(_cat, "resolve_capability", lambda n: None)   # uncatalogued → CLI-via-module
    res = ensure_capability({"name": "cellranger"})
    assert recorded == ["cellranger/7.0.0"], f"CLI module not recorded: {recorded}"
    assert res.get("status") == "ready" and res.get("module") == "cellranger/7.0.0", res
