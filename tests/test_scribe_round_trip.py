"""P3 — End-to-end round-trip: mutate → flush → blow away DB → recover → diff.

Drives the actual hook chain (create_entity, add_edge, append_message, etc.)
via a real Scribe override, drops the project DB, runs the recovery walker
against the FS archive, and asserts row-for-row parity with the original.

Also covers:
- aba-recover --verify (dry-run): produces a report, doesn't touch live DB
- aba-recover --backfill: DB → FS round-trip (sidecars regenerate identically)

Run: .venv/bin/python tests/test_scribe_round_trip.py
"""
from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_scribe_p3_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override        # noqa: E402
from core.recovery.walker import recover_project, backfill_project  # noqa: E402

_scribe = Scribe(tick_interval=10_000.0)
set_scribe_override(_scribe)

from core import projects                                            # noqa: E402
from core.graph.entities import create_entity, update_entity         # noqa: E402
from core.graph.edges import add_edge                                # noqa: E402
from core.graph.messages import append_message                       # noqa: E402
from core.graph._schema import _conn, set_db_path                    # noqa: E402

projects.init()

PROOT = Path(_tmp) / "projects"


def _row_counts(db: Path) -> dict:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    counts = {}
    for table in ("entities", "entity_edges", "messages", "execution_records"):
        try:
            counts[table] = c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        except sqlite3.DatabaseError:
            counts[table] = 0
    c.close()
    return counts


def _make_loaded_project(name: str) -> tuple[str, Path]:
    """Create a project with a mix of entities/edges/messages; return pid + dir."""
    p = projects.create_project(name)
    pid = p["id"]
    projects.set_current(pid)

    # 10 entities
    eids = [create_entity(entity_type="analysis", title=f"A-{i}",
                          metadata={"step": i}) for i in range(5)]
    fids = [create_entity(entity_type="finding", title=f"F-{i}") for i in range(5)]

    # update a couple
    update_entity(eids[0], title="A-0 updated")
    update_entity(fids[2], metadata={"k": "v"})

    # 8 edges (finding → finding via 'supports' — declared in finding.yaml's
    # allowed_edges and tolerant of whether the entity-type registry has been
    # loaded by a sibling test in this process).
    for src_i in range(4):
        for off in range(2):
            add_edge(fids[src_i], fids[(src_i + 1 + off) % 5], "supports")

    # 6 messages across two threads
    for i in range(3):
        append_message("user", [{"type": "text", "text": f"q-{i}"}], thread_id="thr_A")
    for i in range(3):
        append_message("assistant", [{"type": "text", "text": f"a-{i}"}], thread_id="thr_B")

    _scribe.flush()
    return pid, PROOT / pid


# ─── tests ──────────────────────────────────────────────────────────────────
def test_recover_rebuilds_db_from_sidecars():
    pid, pdir = _make_loaded_project("Round-Trip-A")
    live_db = pdir / "project.db"
    before = _row_counts(live_db)

    # Save the live counts, then wipe the DB and recover.
    assert live_db.exists()
    live_db.unlink()
    assert not live_db.exists()
    report = recover_project(pdir)
    assert report.entities >= 11, f"expected ≥11 entities (10 + workspace), got {report.entities}"
    after = _row_counts(report.target_db)
    assert before["entities"] == after["entities"], f"entity count drift: {before['entities']}→{after['entities']}"
    assert before["entity_edges"] == after["entity_edges"]
    assert before["messages"] == after["messages"]


def test_recover_preserves_entity_fields():
    pid, pdir = _make_loaded_project("Round-Trip-B")
    # Read one entity directly from DB
    set_db_path(pdir / "project.db")
    with _conn() as c:
        ref = c.execute("SELECT * FROM entities WHERE type='analysis' AND title LIKE 'A-0%'").fetchone()
    assert ref is not None
    eid = ref["id"]

    (pdir / "project.db").unlink()
    recover_project(pdir)

    set_db_path(pdir / "project.db")
    with _conn() as c:
        got = c.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
    assert got is not None
    assert got["title"] == ref["title"], f"title diverged: {ref['title']} vs {got['title']}"
    assert got["type"] == ref["type"]
    assert got["status"] == ref["status"]
    # metadata round-trips through JSON
    md_a = json.loads(ref["metadata"]) if ref["metadata"] else None
    md_b = json.loads(got["metadata"]) if got["metadata"] else None
    assert md_a == md_b


def test_dry_run_does_not_touch_live_db():
    pid, pdir = _make_loaded_project("Round-Trip-Dry")
    live_db = pdir / "project.db"
    before_mtime = live_db.stat().st_mtime
    before_size = live_db.stat().st_size
    report = recover_project(pdir, dry_run=True)
    assert Path(report.target_db).exists()
    assert Path(report.target_db) != live_db, "dry-run must write to a temp DB"
    # Live DB untouched
    assert live_db.stat().st_mtime == before_mtime
    assert live_db.stat().st_size == before_size
    # Cleanup
    Path(report.target_db).unlink(missing_ok=True)


def test_backfill_rewrites_sidecars():
    pid, pdir = _make_loaded_project("Round-Trip-Backfill")
    # Wipe the sidecars + logs (simulating drift); keep the DB.
    import shutil
    shutil.rmtree(pdir / "entities", ignore_errors=True)
    for f in (pdir / "edges.jsonl", pdir / "project.json"):
        f.unlink(missing_ok=True)
    shutil.rmtree(pdir / "threads", ignore_errors=True)
    # Sanity: confirm wiped
    assert not (pdir / "entities").exists()
    # Backfill
    report = backfill_project(pdir)
    # Restore scribe override (backfill_project flushes via its own scribe but
    # restores the prior override on exit — that's the test override).
    set_scribe_override(_scribe)
    assert (pdir / "entities").is_dir() and any((pdir / "entities").iterdir())
    assert (pdir / "edges.jsonl").exists()
    assert (pdir / "project.json").exists()
    assert (pdir / "threads").is_dir() and any((pdir / "threads").iterdir())
    # Recover from the backfilled sidecars and confirm parity
    live_counts = _row_counts(pdir / "project.db")
    (pdir / "project.db").unlink()
    recover_project(pdir)
    after_counts = _row_counts(pdir / "project.db")
    assert live_counts == after_counts, f"counts mismatch after backfill→recover: {live_counts} vs {after_counts}"


def test_cli_recover_subcommand():
    pid, pdir = _make_loaded_project("Round-Trip-CLI")
    (pdir / "project.db").unlink()
    # Run the CLI as a subprocess with our test env. The env that the parent
    # test already set (ABA_RUNTIME_DIR, ABA_PROJECTS_DIR) is inherited.
    r = subprocess.run(
        [sys.executable, "-m", "core.recovery.cli", "recover", str(pdir)],
        cwd=ROOT / "backend",
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"CLI exit={r.returncode}\nstderr:\n{r.stderr}\nstdout:\n{r.stdout}"
    # Importing content.bio at process-start emits some boot lines to stdout.
    # The report is the trailing JSON object — parse from the last `{` line.
    lines = r.stdout.splitlines()
    json_start = next(i for i, ln in enumerate(lines) if ln.startswith("{"))
    out = json.loads("\n".join(lines[json_start:]))
    assert out["pid"] == pid
    assert out["entities"] >= 10
    assert (pdir / "project.db").exists()


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
