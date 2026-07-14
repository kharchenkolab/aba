"""I2 — Project-ID collision handling at import.

Scenario:
1. Host has an existing project prj_AAAA already in the registry.
2. User rsyncs a separately-authored project (different host) that also has
   pid=prj_AAAA into the target's projects/ dir.
3. `aba-recover recover` should detect the collision, generate a fresh pid,
   rewrite project.json + rename the directory, and proceed.

Run: .venv/bin/python tests/test_scribe_id_collision.py
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
_tmp = tempfile.mkdtemp(prefix="aba_pid_collide_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override   # noqa: E402

_scribe = Scribe(tick_interval=10_000.0)
set_scribe_override(_scribe)

from core import projects                                       # noqa: E402
from core.graph.entities import create_entity                   # noqa: E402
from core.recovery.walker import recover_project                # noqa: E402

projects.init()

PROOT = Path(_tmp) / "projects"


def _make_import_dir_with_pid(pid: str, title: str) -> Path:
    """Synthesize a recovery-shaped project dir at <PROOT>/<pid>/ without
    going through projects.create_project (so the registry stays unaware)."""
    pdir = PROOT / pid
    (pdir / "entities").mkdir(parents=True, exist_ok=True)
    # Minimal project.json
    pdir.joinpath("project.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "pid": pid, "aba_commit": "src-commit", "aba_version": "src-version",
        "source_project_dir": str(pdir),
        "registry": {"id": pid, "name": title,
                     "created_at": "2026-06-01T00:00:00Z",
                     "last_touched": "2026-06-01T00:00:00Z"},
        "project_entity": {"id": "workspace", "type": "workspace", "title": title},
    }))
    # One entity sidecar
    pdir.joinpath("entities", "ana_xx.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "id": "ana_xx", "type": "analysis", "title": "Imported analysis",
        "status": "active",
        "created_at": "2026-06-08T00:00:00Z",
        "updated_at": "2026-06-08T00:00:00Z",
    }))
    return pdir


def test_collision_auto_renames_and_moves_directory():
    # Register an existing project with pid prj_AAA00001
    p_existing = projects.create_project("Existing")
    existing_pid = p_existing["id"]

    # Now place a foreign project at PROOT/<existing_pid>_imported/ but
    # internally claiming the existing pid in project.json.
    foreign = _make_import_dir_with_pid(existing_pid, "Foreign")
    # But the dir name itself matches the colliding pid — the user rsync'd
    # by the source's name into a freshly-empty target slot... wait, can't
    # do that because the existing project already occupies that name.
    # Simulate user putting it into a temp staging dir first.
    staging = PROOT / "_staging_collide"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.move(str(foreign), str(staging))

    # Recover from staging
    report = recover_project(staging)
    assert report.renamed_from_pid == existing_pid, \
        f"expected collision rename, got renamed_from_pid={report.renamed_from_pid}"
    assert report.pid != existing_pid, "new pid must differ"

    # New dir should exist with the new pid name
    new_dir = PROOT / report.pid
    assert new_dir.is_dir(), f"renamed dir not found: {new_dir}"
    assert not staging.exists(), "staging dir should have been moved"

    # project.json should reflect the new pid
    pj = json.loads((new_dir / "project.json").read_text())
    assert pj["pid"] == report.pid
    assert pj["registry"]["id"] == report.pid

    # And the recovered DB should have the entity from the foreign archive
    db = sqlite3.connect(new_dir / "project.db")
    n = db.execute("SELECT COUNT(*) AS n FROM entities WHERE id='ana_xx'").fetchone()[0]
    db.close()
    assert n == 1


def test_no_collision_keeps_pid():
    """Pid not in registry → no rename."""
    pid = "prj_unique99"
    foreign = _make_import_dir_with_pid(pid, "Unique")
    report = recover_project(foreign)
    assert report.renamed_from_pid is None
    assert report.pid == pid
    assert foreign.is_dir(), "directory should not have been moved"


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
