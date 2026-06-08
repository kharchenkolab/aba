"""P1 — Integration test: entity + project mutations flow through scribe.

Wires a controlled Scribe (no background thread, large tick interval) via
set_scribe_override, performs real DB mutations, calls flush() manually,
and asserts the FS sidecars match what we'd expect for recovery.

Covers:
- create_entity → sidecar with right type/title/metadata + project.json fingerprint
- update_entity → sidecar fields updated, status preserved
- archive_entity → sidecar present with status=archived (NOT removed)
- restore_entity → sidecar reverts to status=active
- delete_entity_hard → sidecar unlinked
- create_project → project.json carries the registry row + aba_commit
- rename_project → project.json reflects the new name

Run: .venv/bin/python tests/test_scribe_entity_hooks.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_scribe_p1_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
# Multi-project mode required — create_project + rename_project are no-ops
# in SINGLE mode (ABA_DB_PATH / ABA_DB_PATH_OVERRIDE set). Strip them.
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override  # noqa: E402
from core.graph import _schema as _schema_mod                 # noqa: E402
from core.graph._schema import init_db                        # noqa: E402

# A controlled scribe with a huge tick interval — only flush() drains.
_scribe = Scribe(tick_interval=10_000.0)
set_scribe_override(_scribe)

# Have to defer setting up a real project until after the scribe override is
# in place — the workspace-entity write during create_project should flow
# into _scribe, not the default singleton.
from core import projects                                     # noqa: E402
from core.graph.entities import (                              # noqa: E402
    create_entity, update_entity, archive_entity,
    restore_entity, delete_entity_hard,
)

# Init project sandbox so projects.* + entities.* have a DB to talk to.
projects.init()

PROOT = Path(_tmp) / "projects"


def _entities_dir(pid: str) -> Path:
    return PROOT / pid / "entities"


def _project_json(pid: str) -> Path:
    return PROOT / pid / "project.json"


# ─── tests ──────────────────────────────────────────────────────────────────
def test_create_project_writes_project_json():
    p = projects.create_project("My first project")
    pid = p["id"]
    _scribe.flush()
    pf = _project_json(pid)
    assert pf.exists(), f"project.json should be written, found: {list((PROOT / pid).iterdir())}"
    payload = json.loads(pf.read_text())
    assert payload["pid"] == pid
    assert payload["registry"]["name"] == "My first project"
    assert "aba_commit" in payload and payload["aba_commit"]
    assert "aba_version" in payload


def test_create_entity_writes_sidecar():
    p = projects.create_project("Proj-A")
    pid = p["id"]
    projects.set_current(pid)
    eid = create_entity(entity_type="analysis", title="Analysis A",
                        metadata={"kind": "qc"})
    _scribe.flush()
    sc = _entities_dir(pid) / f"{eid}.json"
    assert sc.exists(), f"sidecar should exist for {eid}; entries={list(_entities_dir(pid).iterdir())}"
    payload = json.loads(sc.read_text())
    assert payload["id"] == eid
    assert payload["title"] == "Analysis A"
    assert payload["type"] == "analysis"
    assert payload["status"] == "active"
    assert payload["metadata"] == {"kind": "qc"}


def test_update_entity_rewrites_sidecar():
    p = projects.create_project("Proj-B")
    pid = p["id"]
    projects.set_current(pid)
    eid = create_entity(entity_type="analysis", title="Initial")
    update_entity(eid, title="Updated", metadata={"note": "changed"})
    _scribe.flush()
    payload = json.loads((_entities_dir(pid) / f"{eid}.json").read_text())
    assert payload["title"] == "Updated"
    assert payload["metadata"] == {"note": "changed"}


def test_archive_entity_marks_status_in_sidecar():
    p = projects.create_project("Proj-C")
    pid = p["id"]
    projects.set_current(pid)
    eid = create_entity(entity_type="analysis", title="To archive")
    archive_entity(eid)
    _scribe.flush()
    sc = _entities_dir(pid) / f"{eid}.json"
    assert sc.exists(), "archived entity sidecar should still exist (status flip, not deletion)"
    payload = json.loads(sc.read_text())
    assert payload["status"] == "archived"


def test_restore_entity_resets_status():
    p = projects.create_project("Proj-D")
    pid = p["id"]
    projects.set_current(pid)
    eid = create_entity(entity_type="analysis", title="To restore")
    archive_entity(eid)
    restore_entity(eid)
    _scribe.flush()
    payload = json.loads((_entities_dir(pid) / f"{eid}.json").read_text())
    assert payload["status"] == "active"


def test_delete_entity_hard_unlinks_sidecar():
    p = projects.create_project("Proj-E")
    pid = p["id"]
    projects.set_current(pid)
    eid = create_entity(entity_type="analysis", title="Doomed")
    _scribe.flush()
    sc = _entities_dir(pid) / f"{eid}.json"
    assert sc.exists()
    delete_entity_hard(eid)
    _scribe.flush()
    assert not sc.exists(), "hard-delete should remove the sidecar"


def test_rename_project_updates_project_json():
    p = projects.create_project("Original name")
    pid = p["id"]
    _scribe.flush()
    projects.rename_project(pid, "Renamed")
    _scribe.flush()
    payload = json.loads(_project_json(pid).read_text())
    assert payload["registry"]["name"] == "Renamed"


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
