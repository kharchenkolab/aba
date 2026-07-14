"""P4 — Edge-log compaction.

- Forcing a small threshold + many edges triggers _maybe_compact_edges.
- A snapshot file is produced; the live tail is rotated to .archived.
- Recovery reads snapshot + new tail seamlessly (already covered by P3, but
  re-verified here).
- gc_archived_edges trims older archives.

Run: .venv/bin/python tests/test_scribe_compaction.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_compact_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override   # noqa: E402

# Tiny threshold + frequent check so compaction fires on a handful of edges.
_scribe = Scribe(tick_interval=10_000.0,
                 compact_threshold_bytes=512,
                 compact_check_every_ticks=1)
set_scribe_override(_scribe)

from core import projects                                       # noqa: E402
from core.graph.entities import create_entity                   # noqa: E402
from core.graph.edges import add_edge                           # noqa: E402
from core.recovery.walker import recover_project, gc_archived_edges  # noqa: E402

projects.init()
PROOT = Path(_tmp) / "projects"


def _populate_edges(pid: str, n: int) -> None:
    """Create n+1 finding entities and connect them in a chain via 'supports'
    (finding→finding declared, so this passes entity-type validation if
    loaded)."""
    fids = [create_entity(entity_type="finding", title=f"F-{i}") for i in range(n + 1)]
    for i in range(n):
        add_edge(fids[i], fids[i + 1], "supports")
    _scribe.flush()


def test_compaction_writes_snapshot_and_archives_tail():
    p = projects.create_project("Compact-A")
    pid = p["id"]
    projects.set_current(pid)
    pdir = PROOT / pid

    # Add enough edges that the log exceeds 512 B (~ 150 B/line × 5 lines)
    _populate_edges(pid, n=10)
    # Manually fire a tick to trigger compaction check.
    _scribe._tick_count += 1
    _scribe._maybe_compact_edges()

    snap = list(pdir.glob("edges-snapshot-*.jsonl"))
    assert len(snap) == 1, f"expected one snapshot, got {[s.name for s in snap]}"
    archived = list(pdir.glob("edges.jsonl.*.archived"))
    assert len(archived) == 1, f"expected one archived tail, got {[a.name for a in archived]}"
    # New tail should be absent (we just rotated) — gets recreated on next add_edge.
    assert not (pdir / "edges.jsonl").exists() or (pdir / "edges.jsonl").stat().st_size == 0

    # Snapshot lines should re-use the regular `add` shape
    snap_rows = [json.loads(ln) for ln in snap[0].read_text().splitlines() if ln.strip()]
    assert snap_rows, "snapshot must contain at least one row"
    for row in snap_rows:
        assert row.get("op") == "add"
        assert row.get("src") and row.get("dst") and row.get("rel")


def test_recovery_after_compaction_rebuilds_full_edge_set():
    p = projects.create_project("Compact-B")
    pid = p["id"]
    projects.set_current(pid)
    pdir = PROOT / pid
    _populate_edges(pid, n=10)
    _scribe._tick_count += 1
    _scribe._maybe_compact_edges()
    # Now add a few more edges (these land in the fresh tail)
    fids = [create_entity(entity_type="finding", title=f"X-{i}") for i in range(3)]
    add_edge(fids[0], fids[1], "supports")
    add_edge(fids[1], fids[2], "supports")
    _scribe.flush()

    # Live edge count
    import sqlite3
    live = sqlite3.connect(pdir / "project.db")
    live_count = live.execute("SELECT COUNT(*) FROM entity_edges").fetchone()[0]
    live.close()

    # Wipe DB; recover from snapshot + tail
    (pdir / "project.db").unlink()
    report = recover_project(pdir)
    assert report.edge_snapshots_read == 1, f"expected to read 1 snapshot, got {report.edge_snapshots_read}"
    assert report.edges_applied >= live_count, \
        f"recovered fewer edges than live ({report.edges_applied} vs {live_count})"

    # Final count parity
    db = sqlite3.connect(pdir / "project.db")
    rebuilt_count = db.execute("SELECT COUNT(*) FROM entity_edges").fetchone()[0]
    db.close()
    assert rebuilt_count == live_count, \
        f"recovered count {rebuilt_count} != live count {live_count}"


def test_gc_archived_edges_trims_older():
    p = projects.create_project("Compact-GC")
    pid = p["id"]
    pdir = PROOT / pid
    pdir.mkdir(parents=True, exist_ok=True)
    # Synthesize three archived tails
    for seq in (1, 2, 3):
        (pdir / f"edges.jsonl.{seq}.archived").write_text("{}\n")
    res = gc_archived_edges(pdir, keep=1)
    assert res == {"deleted": 2, "kept": 1}, f"unexpected gc result: {res}"
    remaining = sorted(p.name for p in pdir.glob("edges.jsonl.*.archived"))
    assert remaining == ["edges.jsonl.3.archived"]


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
