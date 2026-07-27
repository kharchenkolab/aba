"""An exec record's INDEX ROW must live in the project that owns its SIDECAR.

Live (2026-07-27, two workflow sweeps interleaved on one server). A remote run:

  12:08:19  tool starts; the output dir resolves under project A (correct)
  12:08:25  the other sweep calls create_project → the PROCESS-GLOBAL flips to B
  12:08:27  the tool finishes; the index row is INSERTed via the ambient
            connection → lands in B

Project A — which performed the run — ended with zero execution_records, so its
provenance, `reproduce`, and run-scoped output listing had nothing to read.
Project B gained a row whose `record_path` points inside A's directory. Every row
is individually well-formed; the corruption is only visible ACROSS projects.

The window is exactly as wide as the tool call, so any long remote exec racing
any project switch hits it. Two defences, guarded here:

  * the row's DB is DERIVED from the sidecar path — the pair is self-consistent
    by construction, whatever the ambient does mid-call;
  * the turn's project is captured once and threaded through the tool ctx
    (tests/test_project_binding_propagation.py covers the thread-hop half).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

PID_A = "prj_producerAAA"
PID_B = "prj_bystanderBB"


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    """Two initialized project DBs and a repointable ambient binding."""
    from core import config, projects
    from core.graph import _schema

    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", root)
    monkeypatch.setattr(projects, "_single", lambda: False)
    monkeypatch.setattr(projects, "SINGLE", False, raising=False)

    dbs = {}
    for pid in (PID_A, PID_B):
        (root / pid / "work").mkdir(parents=True)
        db = root / pid / "project.db"
        monkeypatch.setattr(_schema, "DB_PATH", db)
        _schema.init_db()
        dbs[pid] = db

    def use(pid):
        """Make `pid` the AMBIENT project, as a project switch would."""
        monkeypatch.setattr(_schema, "DB_PATH", dbs[pid])
        monkeypatch.setitem(projects._state, "current", pid)

    return dbs, use


def _rows(db: Path):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    return list(c.execute("select exec_id, thread_id, record_path "
                          "from execution_records"))


def _create_under(pid, dbs, *, exec_id, thread_id="thr_x"):
    """Write an exec record whose sidecar lives under `pid`'s work dir."""
    from core.graph import exec_records
    cwd = dbs[pid].parent / "work" / f"thread-{thread_id}"
    cwd.mkdir(parents=True, exist_ok=True)
    return exec_records.create(
        exec_id=exec_id, thread_id=thread_id, tool_name="run_python",
        status="ok", started_at="2026-07-27T12:08:19+00:00",
        completed_at="2026-07-27T12:08:27+00:00", cwd=cwd)


# ── the regression ───────────────────────────────────────────────────────────

def test_the_live_race_the_ambient_flips_mid_call(two_projects):
    """THE bug, in its exact shape: the sidecar path is chosen under A, the
    ambient project becomes B before the INSERT."""
    dbs, use = two_projects
    use(PID_A)
    from core.graph import exec_records
    cwd = dbs[PID_A].parent / "work" / "thread-thr_x"
    cwd.mkdir(parents=True, exist_ok=True)
    rp = exec_records.record_path_for(cwd, "exec_race")   # resolved while A is ambient

    use(PID_B)                                            # ← create_project elsewhere
    exec_records.create(exec_id="exec_race", thread_id="thr_x",
                        tool_name="run_python", status="ok",
                        started_at="s", record_path=rp)

    assert [r[0] for r in _rows(dbs[PID_A])] == ["exec_race"], \
        "the producing project has no provenance for a run it performed"
    assert _rows(dbs[PID_B]) == [], \
        "a bystander project gained a row pointing into another project's dir"


def test_row_and_sidecar_always_name_the_same_project(two_projects):
    """The invariant itself, stated positively: for every row, the project
    holding it owns the path it points at."""
    from core.graph.exec_records import project_of_path
    dbs, use = two_projects
    use(PID_A)
    _create_under(PID_A, dbs, exec_id="e1")
    use(PID_B)
    _create_under(PID_B, dbs, exec_id="e2", thread_id="thr_y")
    for pid, db in dbs.items():
        for exec_id, _tid, rp in _rows(db):
            assert project_of_path(rp) == pid, f"{exec_id}: row in {pid}, record in {project_of_path(rp)}"


def test_the_normal_case_is_unchanged(two_projects):
    """CEILING: with no drift the row lands exactly where it always did. A fix
    that routed writes somewhere new would break every single-project install."""
    dbs, use = two_projects
    use(PID_A)
    _create_under(PID_A, dbs, exec_id="e_plain")
    assert [r[0] for r in _rows(dbs[PID_A])] == ["e_plain"]
    assert _rows(dbs[PID_B]) == []


# ── the path→project rule, including the shapes that must NOT resolve ────────

def test_project_of_path(two_projects):
    from core.graph.exec_records import project_of_path
    dbs, _use = two_projects
    root = dbs[PID_A].parent.parent
    assert project_of_path(root / PID_A / "work" / "t" / ".exec" / "e.json") == PID_A
    assert project_of_path(root / PID_B / "artifacts" / "x.csv") == PID_B


def test_paths_outside_the_projects_dir_do_not_resolve(two_projects, tmp_path):
    """WIDE — the degenerate shapes. A tmp/scratch sidecar (tests, single mode)
    has no owning project; inventing one would send the row to a DB that may not
    exist. Falling back to the ambient connection is the correct, pre-existing
    behaviour."""
    from core.graph.exec_records import project_of_path
    assert project_of_path(tmp_path / "elsewhere" / "e.json") is None
    assert project_of_path("/") is None
    assert project_of_path("relative/e.json") is None


def test_a_sidecar_outside_PROJECTS_DIR_still_records(two_projects, tmp_path):
    """ARMED against over-application: the fallback path must still write a row,
    or every test/single-mode exec record silently vanishes."""
    dbs, use = two_projects
    use(PID_A)
    from core.graph import exec_records
    out = tmp_path / "loose"
    out.mkdir()
    exec_records.create(exec_id="e_loose", thread_id="thr_z",
                        tool_name="run_python", status="ok",
                        started_at="s", cwd=out)
    assert "e_loose" in [r[0] for r in _rows(dbs[PID_A])]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
