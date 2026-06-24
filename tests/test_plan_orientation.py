"""Friction-fix A: the workspace orientation attached to the present_plan result
must carry a registered dataset's FULL canonical path (including any subdir), so
the agent uses it verbatim on its first run_python instead of guessing
DATA_DIR/<file> — the pagoda2/prj_0b82b3aa "mistake-first" pattern.

Self-contained (works with the conftest's real-runtime bio pack): creates a
throwaway project, registers a dataset in a subdir, and checks the orientation.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio


def test_orientation_surfaces_registered_dataset_subdir_path():
    from core import projects
    from core.config import project_data_dir
    from core.graph.entities import create_entity
    from content.bio.tools import register_dataset_tool
    from content.bio.tools.run_exec import _prior_run_files_preamble, _run_scratch_cwd
    from content.bio.lifecycle.runs import active_run_id

    p = projects.create_project("a-orient-test")
    pid = p["id"] if isinstance(p, dict) else p
    try:
        projects.set_current(pid)
        sub = Path(project_data_dir(pid)) / "geo_sub_orient"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "S1_matrix.mtx.gz").write_bytes(b"x")
        tid = create_entity(entity_type="thread", title="t", metadata={"thread_id": None})
        res = register_dataset_tool({"path": "geo_sub_orient", "title": "GEO sub"},
                                    {"thread_id": tid})
        assert res.get("status") == "ok", res
        # The exact call guide.py makes when present_plan opens the Run.
        orient = _prior_run_files_preamble(pid, str(tid),
                                           current_run_id=active_run_id(str(tid)),
                                           cwd=_run_scratch_cwd(pid, str(tid)))
        assert "geo_sub_orient" in orient, f"orientation must carry the subdir path:\n{orient}"
        assert "canonical paths" in orient.lower()
    finally:
        try:
            projects.delete_project(pid)
        except Exception:  # noqa: BLE001 — cleanup best-effort
            pass
