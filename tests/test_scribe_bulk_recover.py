"""I4 — bulk recovery (--all-under).

Builds three projects, drops their DBs, runs the bulk recovery against
runtime/projects/, and asserts each was rebuilt. Also drops a junk dir
under projects/ to confirm tolerance for non-project subdirs.

Run: .venv/bin/python tests/test_scribe_bulk_recover.py
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_bulk_")
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

projects.init()

PROOT = Path(_tmp) / "projects"


def _populate(name: str, n_entities: int) -> str:
    p = projects.create_project(name)
    pid = p["id"]
    projects.set_current(pid)
    for i in range(n_entities):
        create_entity(entity_type="analysis", title=f"{name}-{i}")
    _scribe.flush()
    return pid


# ─── tests ──────────────────────────────────────────────────────────────────
def test_bulk_recover_rebuilds_every_project():
    pids = [
        _populate("Bulk-A", 3),
        _populate("Bulk-B", 5),
        _populate("Bulk-C", 2),
    ]
    # Drop every project DB
    for pid in pids:
        (PROOT / pid / "project.db").unlink()
        assert not (PROOT / pid / "project.db").exists()
    # Drop a non-project dir to confirm it's skipped
    junk = PROOT / "_junkdir"
    junk.mkdir(exist_ok=True)
    (junk / "random_file.txt").write_text("not a project")

    r = subprocess.run(
        [sys.executable, "-m", "core.recovery.cli", "all-under", str(PROOT)],
        cwd=ROOT / "backend",
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"CLI exit={r.returncode}\nstderr:\n{r.stderr}"
    # Parse trailing JSON from stdout (tolerate import-time bio prints)
    lines = r.stdout.splitlines()
    json_start = next(i for i, ln in enumerate(lines) if ln.startswith("{"))
    out = json.loads("\n".join(lines[json_start:]))
    assert "recovered" in out
    recovered_pids = {r["pid"] for r in out["recovered"]}
    assert set(pids).issubset(recovered_pids), \
        f"missing pids: {set(pids) - recovered_pids}"
    # No failed entries (junk dir lacks project.json so it's silently skipped,
    # not added to "failed")
    assert out["failed"] == [], f"unexpected failures: {out['failed']}"
    # Each recovered project's DB now exists
    for pid in pids:
        assert (PROOT / pid / "project.db").exists()


def test_bulk_recover_dry_run_does_not_touch_dbs():
    p = _populate("Bulk-Dry", 2)
    db = PROOT / p / "project.db"
    before_mtime = db.stat().st_mtime
    before_size = db.stat().st_size

    r = subprocess.run(
        [sys.executable, "-m", "core.recovery.cli", "all-under", str(PROOT), "--dry-run"],
        cwd=ROOT / "backend",
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"CLI exit={r.returncode}\nstderr:\n{r.stderr}"
    # Live DB untouched (dry-run writes to tempfiles)
    assert db.stat().st_mtime == before_mtime
    assert db.stat().st_size == before_size


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
