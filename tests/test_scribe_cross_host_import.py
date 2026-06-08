"""I1 — Cross-host import: path normalization at recovery time.

Simulates a project authored under one runtime root, then physically moved
to a different runtime root (e.g. rsync'd from another machine). The recovery
walker must rewrite absolute paths so artifacts + exec records resolve under
the target runtime.

Setup:
- Create a project under runtime_A (host A's PROJECTS_DIR).
- Populate entities with artifact_path pointing inside runtime_A.
- Drop a fake .exec sidecar to simulate provenance.
- Move the project dir into runtime_B/projects/<pid>/.
- Run recover_project; assert artifact_path + record_path get rewritten.

Run: .venv/bin/python tests/test_scribe_cross_host_import.py
"""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# We need to flip ABA_PROJECTS_DIR between host A and host B during the test —
# simplest pattern is to start under host A, build a project, then physically
# rename the dir + repoint env vars + reload core.config.
_runtime_a = tempfile.mkdtemp(prefix="aba_xhost_A_")
os.environ["ABA_RUNTIME_DIR"] = _runtime_a
os.environ["ABA_PROJECTS_DIR"] = str(Path(_runtime_a) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_runtime_a) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_runtime_a) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override   # noqa: E402

_scribe = Scribe(tick_interval=10_000.0)
set_scribe_override(_scribe)

from core import projects                                       # noqa: E402
from core.graph.entities import create_entity                   # noqa: E402

projects.init()


def _build_project_on_host_a() -> tuple[str, Path]:
    p = projects.create_project("HostA Project")
    pid = p["id"]
    projects.set_current(pid)
    pdir = Path(os.environ["ABA_PROJECTS_DIR"]) / pid

    # Entity whose artifact_path is rooted at host A's runtime.
    artifact_under_a = pdir / "artifacts" / "fake.png"
    artifact_under_a.parent.mkdir(parents=True, exist_ok=True)
    artifact_under_a.write_bytes(b"\x89PNG\r\n\x1a\n")
    eid = create_entity(
        entity_type="figure", title="Plot",
        artifact_path=str(artifact_under_a),
    )

    # Fake exec sidecar at <pdir>/work/run_x/.exec/exec_aaa.json
    exec_dir = pdir / "work" / "run_x" / ".exec"
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "exec_aaa.json").write_text(json.dumps({
        "exec_id": "exec_aaa",
        "thread_id": "thr_t",
        "tool_name": "run_python",
        "status": "ok",
        "started_at": "2026-06-08T00:00:00Z",
        "completed_at": "2026-06-08T00:00:01Z",
    }))

    _scribe.flush()
    return pid, pdir


def _move_to_host_b(pid_dir: Path, pid: str) -> Path:
    """Physically move <runtime_A>/projects/<pid>/ → <runtime_B>/projects/<pid>/
    and repoint the test's runtime env to host B. Returns the new dir path."""
    runtime_b = tempfile.mkdtemp(prefix="aba_xhost_B_")
    new_projects = Path(runtime_b) / "projects"
    new_projects.mkdir(parents=True, exist_ok=True)
    new_pdir = new_projects / pid
    shutil.move(str(pid_dir), str(new_pdir))
    # Repoint env
    os.environ["ABA_RUNTIME_DIR"] = runtime_b
    os.environ["ABA_PROJECTS_DIR"] = str(new_projects)
    # Force core.config to pick up the new env by reloading; the PROJECTS_DIR
    # constant is resolved at import time but project_root() reads
    # PROJECTS_DIR — which we patch by reimporting the module.
    import importlib
    import core.config as _cfg
    importlib.reload(_cfg)
    return new_pdir


# ─── tests ──────────────────────────────────────────────────────────────────
def test_path_normalization_rewrites_artifact_and_record_paths():
    # Build under host A
    pid, pdir_a = _build_project_on_host_a()
    # Stash original absolute paths
    src_artifact = str((pdir_a / "artifacts" / "fake.png").resolve())
    src_exec_sidecar = str((pdir_a / "work" / "run_x" / ".exec" / "exec_aaa.json").resolve())

    # Confirm scribe stamped source_project_dir
    pj = json.loads((pdir_a / "project.json").read_text())
    assert pj.get("source_project_dir") == str(pdir_a.resolve()), \
        f"scribe should stamp source_project_dir, got: {pj.get('source_project_dir')}"

    # Move to host B
    pdir_b = _move_to_host_b(pdir_a, pid)

    # Drop the original DB so recover_project rebuilds from sidecars
    (pdir_b / "project.db").unlink()

    # Recover under host B
    from core.recovery.walker import recover_project   # noqa: PLC0415
    report = recover_project(pdir_b)
    assert any("cross-host" in w for w in report.warnings), \
        f"expected cross-host warning, got: {report.warnings}"

    # Now confirm the DB has rewritten paths
    db = sqlite3.connect(pdir_b / "project.db")
    db.row_factory = sqlite3.Row
    art = db.execute("SELECT artifact_path FROM entities WHERE type='figure'").fetchone()
    rec = db.execute("SELECT record_path FROM execution_records WHERE exec_id='exec_aaa'").fetchone()
    db.close()

    # artifact_path must now live under host B's project dir
    assert art["artifact_path"].startswith(str(pdir_b)), \
        f"artifact_path not rewritten: {art['artifact_path']}"
    assert not art["artifact_path"].startswith(str(pdir_a)), \
        f"artifact_path still has host-A prefix: {art['artifact_path']}"
    # record_path must point at the moved sidecar (host B)
    assert rec["record_path"].startswith(str(pdir_b)), \
        f"record_path not rewritten: {rec['record_path']}"


def test_same_host_recover_is_a_noop_for_paths():
    """If source_project_dir == target project dir, no normalization fires."""
    # Build under host B (the current env after the prior test moved us there)
    p = projects.create_project("SameHost Project")
    pid = p["id"]
    projects.set_current(pid)
    pdir = Path(os.environ["ABA_PROJECTS_DIR"]) / pid
    art = pdir / "artifacts" / "x.png"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_bytes(b"\x89PNG")
    create_entity(entity_type="figure", title="X", artifact_path=str(art))
    _scribe.flush()
    (pdir / "project.db").unlink()

    from core.recovery.walker import recover_project   # noqa: PLC0415
    report = recover_project(pdir)
    # No warning about cross-host
    assert not any("cross-host" in w for w in report.warnings), \
        f"unexpected cross-host warning on same-host recover: {report.warnings}"

    db = sqlite3.connect(pdir / "project.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT artifact_path FROM entities WHERE type='figure'").fetchone()
    db.close()
    assert row["artifact_path"] == str(art), \
        f"artifact_path unexpectedly rewritten on same-host: {row['artifact_path']}"


# ─── runner ─────────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
