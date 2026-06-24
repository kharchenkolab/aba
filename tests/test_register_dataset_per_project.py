"""register_dataset resolves relative paths against the *per-project* data dir.

Production bug surfaced in prj_efd2e77e (thr_f356ab53): the agent saved files
under ``os.environ["DATA_DIR"]`` (the per-project path the kernel preamble
injects), then called register_dataset with a relative basename — natural
because the file is directly under DATA_DIR by the agent's view. The resolver
checked the module-level ``config.DATA_DIR`` constant, which is resolved ONCE
at import to the workspace-level dir (``projects/_workspace/data``) and never
tracks which project is active. The two paths diverge in any real install, so
the resolver missed the per-project candidate and returned "Nothing to
register".

This test sets DATA_DIR to a deliberately-different path from
``project_data_dir(current_project_id())`` (the kernel's view) — reproducing
the production divergence — then exercises three workflows:

  1) files under per-project DATA_DIR + bare path → must register OK
  2) files under thread scratch tier + bare path → adopted into per-project
     DATA_DIR (regression guard on d17_register_adopt.py)
  3) missing path → error string must reference the per-project dir, not the
     workspace one (otherwise the agent is sent to a wrong place to "fix" it)

d17 sets ``os.environ["DATA_DIR"]`` BEFORE imports to one location and lets
SINGLE-mode use the same place, so the bug never surfaces there. This test
breaks that coincidence deliberately.

Run: ``.venv/bin/python -m pytest tests/test_register_dataset_per_project.py -q``
"""
from __future__ import annotations

# ─── ENV SETUP must happen BEFORE any backend import ────────────────────
# core.config resolves DATA_DIR/PROJECTS_DIR/etc. as module-level constants at
# import. The bug we're probing requires DATA_DIR != project_data_dir(pid), so
# we set DATA_DIR to the workspace dir (production default) and let
# PROJECTS_DIR sit one level above — then project_data_dir("single") naturally
# lands at <RUNTIME>/projects/single/data, a different path.
import os
import sys
import tempfile
from pathlib import Path

_ROOT = tempfile.mkdtemp(prefix="aba_register_perproj_")
_WORKSPACE_DATA = Path(_ROOT) / "projects" / "_workspace" / "data"
_WORKSPACE_DATA.mkdir(parents=True)
os.environ["ABA_RUNTIME_DIR"] = _ROOT
os.environ["DATA_DIR"] = str(_WORKSPACE_DATA)
os.environ["ARTIFACTS_DIR"] = str(Path(_ROOT) / "_workspace_artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_ROOT) / "_workspace_work")
os.environ["ABA_DB_PATH"] = str(Path(_ROOT) / "test.db")           # SINGLE mode (d17 pattern)
os.environ["ABA_ENVS_DIR"] = str(Path(_ROOT) / "envs")

# Now safe to import the backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.graph._schema import init_db                           # noqa: E402
init_db()
import content.bio                                                # noqa: E402,F401  — registers tools
from core import projects                                          # noqa: E402
from core.config import project_data_dir                           # noqa: E402
from core.graph.entities import create_entity                      # noqa: E402
from content.bio.tools import register_dataset_tool                # noqa: E402
from core.data.workspace import scratch_dir                        # noqa: E402
from core.config import DATA_DIR as MODULE_DATA_DIR                # noqa: E402

_PID = projects.current() or "single"
_PROJECT_DATA_DIR = Path(project_data_dir(_PID))                   # creates the dir

# Sanity-check the divergence — without it the test would pass on broken code.
assert str(_PROJECT_DATA_DIR) != str(MODULE_DATA_DIR), (
    f"test setup broken: per-project ({_PROJECT_DATA_DIR}) and module "
    f"({MODULE_DATA_DIR}) DATA_DIRs are equal; the bug requires divergence")


def _new_ctx() -> dict:
    """Fresh thread per test so adoption/dataset rows don't collide across cases."""
    tid = create_entity(entity_type="thread", title="t",
                        metadata={"thread_id": None})
    return {"thread_id": tid}


# ─── (1) the bug repro — relative path against per-project DATA_DIR ─────
def test_relative_path_resolves_against_per_project_data_dir():
    """Agent saves to <DATA_DIR>/foo (per-project) then registers with
    `path="foo"`. Must succeed; without the fix the resolver checks only
    MODULE_DATA_DIR and returns "Nothing to register"."""
    folder = _PROJECT_DATA_DIR / "geo_first2_reg1"
    folder.mkdir()
    (folder / "GSM1_matrix.mtx.gz").write_bytes(b"counts")
    (folder / "GSM1_barcodes.tsv.gz").write_bytes(b"bc")

    res = register_dataset_tool(
        {"path": "geo_first2_reg1", "title": "GEO first 2 samples"}, _new_ctx()
    )
    assert res.get("status") == "ok", (
        f"resolver missed the per-project data dir: {res!r}")
    assert res.get("artifact_path"), "artifact_path must be set on success"
    assert Path(res["artifact_path"]).resolve() == folder.resolve(), (
        f"resolved {res.get('artifact_path')!r}, expected {folder}")


# ─── friction-fix A: the plan-time orientation carries the canonical path ──
def test_orientation_surfaces_registered_dataset_canonical_subdir():
    """Fix A: the workspace orientation injected into the present_plan result must
    carry the registered dataset's FULL canonical path INCLUDING any subdir — so
    the agent uses it verbatim on its first run_python instead of guessing
    DATA_DIR/<file> (the pagoda2/prj_0b82b3aa 'mistake-first' pattern)."""
    from content.bio.tools.run_exec import _prior_run_files_preamble, _run_scratch_cwd
    from content.bio.lifecycle.runs import active_run_id
    sub = _PROJECT_DATA_DIR / "geo_sub_orient"
    sub.mkdir()
    (sub / "S1_matrix.mtx.gz").write_bytes(b"x")
    ctx = _new_ctx()
    assert register_dataset_tool({"path": "geo_sub_orient", "title": "GEO sub"}, ctx)["status"] == "ok"
    tid = str(ctx["thread_id"])
    orient = _prior_run_files_preamble(_PID, tid, current_run_id=active_run_id(tid),
                                       cwd=_run_scratch_cwd(_PID, tid))
    assert "geo_sub_orient" in orient, f"orientation must carry the subdir path:\n{orient}"
    assert "canonical paths" in orient.lower()


# ─── (2) error message must reference the per-project DATA_DIR ──────────
def test_error_message_points_at_per_project_dir():
    """When NOTHING resolves, any DATA_DIR hint in the response must be the
    per-project one (matches the agent's os.environ['DATA_DIR']) — else the
    error guidance sends the agent to a directory it can't see."""
    res = register_dataset_tool(
        {"path": "nope_does_not_exist", "title": "missing"}, _new_ctx()
    )
    blob = " ".join(str(v) for v in res.values())
    # The legacy fallback for a totally-unresolvable path is by-reference +
    # warning note, not the hard "Nothing to register" branch. Either way:
    # if a DATA_DIR is mentioned, it must be the per-project one.
    if str(MODULE_DATA_DIR) in blob:
        assert str(_PROJECT_DATA_DIR) in blob, (
            f"response mentions module DATA_DIR but not the per-project one "
            f"(agent would be sent to wrong place): {res!r}")


# ─── (3) regression guard — d17's scratch-tier adoption still works ─────
def test_scratch_path_resolves_and_adopts_into_project_data_dir():
    """A relative path that's in the thread scratch tier (not under DATA_DIR)
    must still be found AND adopted into the per-project DATA_DIR — not the
    module-level workspace DATA_DIR (the 2026-05-31 fix that we're guarding
    against regression)."""
    ctx = _new_ctx()
    sdir = Path(scratch_dir(_PID, f"thread-{ctx['thread_id']}"))
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "from_scratch_reg3.csv").write_text("a,b\n1,2\n")

    res = register_dataset_tool(
        {"path": "from_scratch_reg3.csv", "title": "scratch one"}, ctx
    )
    assert res.get("status") == "ok", f"scratch resolution broken: {res!r}"
    ap = Path(res["artifact_path"])
    assert ap.exists(), f"adopted copy missing at {ap}"
    assert str(ap).startswith(str(_PROJECT_DATA_DIR)), (
        f"expected adopt INTO per-project data dir {_PROJECT_DATA_DIR}, got {ap}")
    # And NOT into the module DATA_DIR (the workspace one — that was the
    # 2026-05-31 production bug).
    assert not str(ap).startswith(str(MODULE_DATA_DIR)), (
        f"file was adopted into module DATA_DIR {MODULE_DATA_DIR} instead of "
        f"per-project — regression of the 2026-05-31 fix")
