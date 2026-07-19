"""W3.4: weft_envs.json writes must be atomic + serialized — a named-env create
racing the default-session write (or parallel tool calls) must not lose either
(observed live: an isolated env vanished under a concurrent default write).
"""
from __future__ import annotations
import os, sys, tempfile, threading
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_envconc_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core.compute import named_envs  # noqa: E402


def test_concurrent_env_and_default_writes_do_not_clobber():
    pid = "cproj"
    Path(_tmp, "projects", pid).mkdir(parents=True, exist_ok=True)
    barrier = threading.Barrier(2)

    def add_named():
        barrier.wait()
        for i in range(40):
            named_envs._update(pid, lambda d, i=i: d["envs"].__setitem__(
                f"e{i}", {"env_id": f"env:v1:{i}", "language": "python"}))

    def add_default():
        barrier.wait()
        for i in range(40):
            named_envs._update(pid, lambda d, i=i: d.setdefault("default", {}).__setitem__(
                "python", {"session_id": f"s{i}"}))

    t1 = threading.Thread(target=add_named); t2 = threading.Thread(target=add_default)
    t1.start(); t2.start(); t1.join(); t2.join()

    data = named_envs._load(pid)
    assert len(data["envs"]) == 40, f"lost named-env writes: {len(data['envs'])}/40"
    assert data["default"].get("python") is not None   # default survived too


def test_save_is_atomic(monkeypatch):
    """A crash mid-write must not corrupt the file (temp + replace)."""
    pid = "aproj"
    Path(_tmp, "projects", pid).mkdir(parents=True, exist_ok=True)
    named_envs._update(pid, lambda d: d["envs"].__setitem__("keep", {"env_id": "x"}))
    p = named_envs._registry_path(pid)
    good = p.read_text()
    # a failed write leaves the original intact (temp file discarded)
    orig_replace = os.replace
    def boom(a, b): raise OSError("disk full")
    monkeypatch.setattr(named_envs._os, "replace", boom)
    try:
        named_envs._update(pid, lambda d: d["envs"].__setitem__("new", {"env_id": "y"}))
    except OSError:
        pass
    monkeypatch.setattr(named_envs._os, "replace", orig_replace)
    assert p.read_text() == good          # original preserved
    assert not list(p.parent.glob("*.tmp.*"))  # no orphan temp (best-effort)
