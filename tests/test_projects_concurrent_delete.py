"""Concurrent DELETE on the project registry must NOT lose data.

Background: 2026-06-10 incident. A bulk cleanup hit /api/projects/{pid}
with `xargs -P 4` (256 deletes against ~261 projects). The
delete_project helper did `reg = _load(); reg.remove(pid); _save(reg)`
on registry.json — unlocked. Concurrent workers each loaded the same
snapshot, mutated independently, and the last writer won, leaving the
registry empty. Five projects the script tried to keep were nuked from
the registry because their state was overwritten by stale snapshots.

Fix: `_locked_registry()` context manager around every read-modify-write,
holding an exclusive flock on a sidecar `registry.lock`. This test
proves the fix by:
  1. Seeding the registry with 50 fake projects.
  2. Firing 40 concurrent `delete_project()` calls from a thread pool.
  3. Asserting the final registry has exactly the 10 survivors and
     none of the 40 deleted pids leaked back.

Run: .venv/bin/python tests/test_projects_concurrent_delete.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_reg_race_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
sys.path.insert(0, str(ROOT / "backend"))

# Module-level constant override — env-var-only isolation fails when
# conftest.py imports core.runtime.content_pack at COLLECTION time,
# which transitively loads core.config before this file's `os.environ`
# assignments above are seen. By then PROJECTS_DIR is already resolved
# to /workspace/aba-runtime/projects. We monkey-patch the resolved
# constants on the modules so our writes land in the temp dir even
# under that collection order. Verified by running this test pre-fix
# and observing the live registry get clobbered.
import core.config as _cc  # noqa: E402
from core import projects  # noqa: E402

_TEST_PROJECTS = Path(_tmp) / "projects"
_TEST_PROJECTS.mkdir(parents=True, exist_ok=True)
_cc.PROJECTS_DIR = _TEST_PROJECTS
projects.PROJECTS_DIR = _TEST_PROJECTS
projects.REGISTRY = _TEST_PROJECTS / "registry.json"
projects.SCRATCH = _TEST_PROJECTS / "_scratch.db"
# A sibling test (test_direct_api_runtime_skeleton.py) sets ABA_DB_PATH
# at import time which flips projects.SINGLE = True process-wide, making
# delete_project early-return. Force it off so our race test exercises
# the real registry-write path.
projects.SINGLE = False

PROJECTS_DIR = _TEST_PROJECTS
REG_FILE = PROJECTS_DIR / "registry.json"


def seed_registry(n: int) -> list[str]:
    """Write N fake project rows into the registry and return their pids."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    pids = [f"prj_{i:08x}" for i in range(n)]
    rows = [{"id": pid, "name": f"p{i}",
             "created_at": "2026-06-10T00:00:00+00:00",
             "last_touched": "2026-06-10T00:00:00+00:00"}
            for i, pid in enumerate(pids)]
    REG_FILE.write_text(json.dumps(rows, indent=2))
    # Materialize an (empty) DB file per pid so delete_project's f.unlink()
    # has something to unlink — without this, delete still works but the
    # branch is untested.
    for pid in pids:
        d = PROJECTS_DIR / pid
        d.mkdir(exist_ok=True)
        (d / "project.db").write_bytes(b"")
    return pids


def test_concurrent_delete_loses_no_data():
    pids = seed_registry(50)
    assert len(json.loads(REG_FILE.read_text())) == 50, "seed failed"

    # Delete 40 of the 50 in parallel — same shape as the failed xargs -P 4.
    targets = pids[:40]
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(projects.delete_project, targets))

    remaining = json.loads(REG_FILE.read_text())
    remaining_ids = {p["id"] for p in remaining}

    # The 10 survivors must all be present.
    expected_survivors = set(pids[40:])
    missing = expected_survivors - remaining_ids
    assert not missing, f"survivors lost to race: {sorted(missing)}"

    # None of the deleted pids may resurrect.
    leaked = set(targets) & remaining_ids
    assert not leaked, f"deleted pids resurrected: {sorted(leaked)}"

    # And the count is exactly right (no junk rows).
    assert len(remaining) == 10, f"expected 10 rows, got {len(remaining)}"

    print(f"OK: 40 concurrent deletes, 10 survivors intact (max_workers=8)")


def test_concurrent_append_via_locked_registry():
    """Direct test of _locked_registry() under concurrent appends — the
    other half of the race (creates lose rows the same way deletes do
    when the read-modify-write isn't atomic). Stays at the helper layer
    so it doesn't need DB/schema scaffolding to exercise the lock."""
    # Fresh registry
    REG_FILE.unlink(missing_ok=True)

    def add(i: int) -> None:
        with projects._locked_registry() as reg:
            reg.append({"id": f"prj_n{i:04d}", "name": f"n{i}",
                        "created_at": "2026-06-10T00:00:00+00:00",
                        "last_touched": "2026-06-10T00:00:00+00:00"})

    N = 30
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(add, range(N)))

    rows = json.loads(REG_FILE.read_text())
    assert len(rows) == N, f"lost rows: expected {N}, got {len(rows)}"
    names = sorted(r["name"] for r in rows)
    assert names == sorted(f"n{i}" for i in range(N)), \
        f"name set drifted: {names}"
    print(f"OK: {N} concurrent _locked_registry() appends, all rows landed")


if __name__ == "__main__":
    test_concurrent_delete_loses_no_data()
    test_concurrent_append_via_locked_registry()
    print("\nALL PASS")
