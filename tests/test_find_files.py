"""find_files — glob-style file search across the project tree.

Live friction (prj_8143327c thr_80190faf, 2026-06-16): agent had no
file-finding tool, fell back to subprocess.run(['find', ...]) inside
run_python whenever it needed to locate a saved file. That works
from Python but is awkward from R — and twice in one session the
agent went 'run_r fails → switch to run_python → call shell find →
read the path → switch back to run_r'. find_files collapses that
into one tool call, callable from any language context.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_find_files_")
os.environ["ABA_DB_PATH"]     = os.path.join(_tmp, "ff.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_WORK_DIR"]    = os.path.join(_tmp, "work")
os.environ["ABA_ENVS_DIR"]    = os.path.join(_tmp, "envs")
os.environ["DATA_DIR"]        = os.path.join(_tmp, "data")
os.environ["ARTIFACTS_DIR"]   = os.path.join(_tmp, "artifacts")

from core.graph._schema import init_db                              # noqa: E402
init_db()

import content.bio                                                  # noqa: E402,F401
from core.runtime.mcp import (                                       # noqa: E402
    register_inprocess_server, _reset_for_testing,
)
from content.bio.mcp_servers.aba_core import make_server             # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)


def _call(name: str, args: dict) -> dict:
    from content.bio.tools import execute_tool
    raw = execute_tool(name, args, {"thread_id": "thr_find"})
    return json.loads(raw) if isinstance(raw, str) else raw


def _pid_dirs():
    from core.config import (PROJECTS_DIR, current_project_id,
                              project_work_dir, project_data_dir,
                              ARTIFACTS_DIR)
    pid = current_project_id()
    return {
        "pid": pid,
        "work": project_work_dir(pid),
        "data": project_data_dir(pid),
        "artifacts": Path(ARTIFACTS_DIR) / pid,
        "project_root": PROJECTS_DIR / pid,
    }


def _seed_project():
    """Set the current project + create a few files in the standard
    locations so we have something concrete to find."""
    from core import projects
    projects.set_current("prj_find_test")
    d = _pid_dirs()
    # Run scratch with a .rds + a .png (the live-bug shape)
    run_dir = d["work"] / "ana_e92634df"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "seurat_integrated.rds").write_bytes(b"x" * 1000)
    (run_dir / "umap_annotated.png").write_bytes(b"y" * 500)
    # Thread scratch — a stray CSV
    th = d["work"] / "thread-thr_find"
    th.mkdir(parents=True, exist_ok=True)
    (th / "tmp_genes.csv").write_text("gene,n\nA,1\n")
    # DATA_DIR — a registered dataset folder
    ds = d["data"] / "geo_data"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "GSM5746259_matrix.mtx.gz").write_bytes(b"z" * 100)
    # Artifacts — a harvested figure
    d["artifacts"].mkdir(parents=True, exist_ok=True)
    (d["artifacts"] / "abc123.png").write_bytes(b"a" * 100)
    # Noise we should NOT find
    (run_dir / ".exec").mkdir(exist_ok=True)
    (run_dir / ".exec" / "exec_xyz.json").write_text("{}")
    (run_dir / "__pycache__").mkdir(exist_ok=True)
    (run_dir / "__pycache__" / "garbage.pyc").write_bytes(b"")


def test_finds_rds_under_default_project_root():
    """The exact live-bug shape: agent wants to find seurat_integrated.rds
    after a kernel restart. One call returns the absolute path."""
    _seed_project()
    res = _call("find_files", {"pattern": "*.rds"})
    assert res.get("matches"), res
    names = [m["name"] for m in res["matches"]]
    assert "seurat_integrated.rds" in names
    paths = [m["path"] for m in res["matches"]]
    assert any(p.endswith("/work/ana_e92634df/seurat_integrated.rds")
               for p in paths), paths


def test_root_work_scopes_to_scratch_tree():
    _seed_project()
    res = _call("find_files", {"pattern": "*.csv", "root": "work"})
    names = [m["name"] for m in res["matches"]]
    assert "tmp_genes.csv" in names
    # And the search root is the work dir, not the project root
    assert res["root_path"].endswith("/work")


def test_root_data_scopes_to_registered_datasets():
    _seed_project()
    res = _call("find_files",
                {"pattern": "GSM*", "root": "data"})
    assert res.get("matches"), res
    assert any("GSM5746259" in m["name"] for m in res["matches"])


def test_root_artifacts_scopes_to_harvested_figs():
    _seed_project()
    res = _call("find_files",
                {"pattern": "*.png", "root": "artifacts"})
    assert res.get("matches"), res
    names = [m["name"] for m in res["matches"]]
    assert "abc123.png" in names
    # The harvested figure is in artifacts, the run-scratch png is not
    assert "umap_annotated.png" not in names


def test_skips_noisy_dirs():
    """Default walk skips .exec, __pycache__, node_modules, .git."""
    _seed_project()
    res = _call("find_files", {"pattern": "*.json"})
    # The .exec/exec_xyz.json is filtered by the skip-dir rule
    assert all(".exec" not in m["path"] for m in res["matches"]), res
    res2 = _call("find_files", {"pattern": "*.pyc"})
    assert all("__pycache__" not in m["path"] for m in res2["matches"]), res2


def test_matches_sorted_newest_first():
    """Reload-after-restart UX: the freshest save is usually the one
    the agent wants."""
    _seed_project()
    d = _pid_dirs()
    older = d["work"] / "ana_e92634df" / "seurat_integrated.rds"
    newer = d["work"] / "ana_e92634df" / "intermediate.rds"
    newer.write_bytes(b"n" * 100)
    # Bump newer's mtime explicitly so the test isn't fs-clock-dependent.
    import time
    os.utime(older, (time.time() - 60, time.time() - 60))
    os.utime(newer, (time.time(),         time.time()))
    res = _call("find_files", {"pattern": "*.rds"})
    assert res["matches"][0]["name"] == "intermediate.rds", res


def test_rejects_slash_in_pattern():
    """basename glob only — paths with '/' get a clean refusal so the
    agent fixes the call instead of getting an empty result."""
    _seed_project()
    res = _call("find_files", {"pattern": "work/*.rds"})
    assert "error" in res, res
    assert "basename" in res["error"].lower(), res


def test_rejects_unknown_root():
    """Unknown root → caught by pydantic Literal validation upstream
    (FastMCP), so the error frame is the gateway's, not ours. Either
    surface is fine for the agent — it gets a clear 'use one of …'
    message either way."""
    _seed_project()
    res = _call("find_files", {"pattern": "*.rds", "root": "everything"})
    msg = json.dumps(res)
    assert "error" in res or res.get("status") == "error", res
    # The Literal validation names the valid choices.
    assert "project" in msg and "work" in msg, res


def test_max_results_truncates():
    _seed_project()
    d = _pid_dirs()
    run = d["work"] / "ana_e92634df"
    for i in range(10):
        (run / f"a_{i}.png").write_bytes(b"x")
    res = _call("find_files",
                {"pattern": "a_*.png", "max_results": 3})
    assert len(res["matches"]) == 3
    assert res["truncated"] is True


def test_empty_match_returns_clean_shape():
    _seed_project()
    res = _call("find_files", {"pattern": "nonexistent.xyz"})
    assert "error" not in res
    assert res["matches"] == []
    assert res["truncated"] is False


def test_match_metadata_shape():
    """Each match carries size + mtime so the agent can pick the right
    candidate when multiple files share a name."""
    _seed_project()
    res = _call("find_files", {"pattern": "seurat_integrated.rds"})
    m = res["matches"][0]
    assert set(m.keys()) >= {"name", "path", "size_bytes", "mtime"}
    assert m["size_bytes"] == 1000
    assert "T" in m["mtime"] and m["mtime"].endswith("+00:00")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
