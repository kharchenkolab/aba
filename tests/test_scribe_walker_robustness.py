"""P6 — Deterministic walker robustness.

Targets specific failure-mode boundaries the walker must tolerate. No
random fuzz harness — each case is ~10 lines and reproduces exactly.

Coverage:
- Torn (unterminated) trailing line in edges.jsonl.
- Truncated / unparseable entity sidecar.
- Edge referencing an unknown endpoint (orphan).
- `clear` sentinel mid-thread log clears prior messages.
- Out-of-order seq in edges log; idempotent replay converges.
- Snapshot + tail with overlapping seqs (idempotency).

Plus one end-to-end kill-smoke:
- Spawn a worker subprocess that issues mutations, kill it mid-stream,
  recover, assert bounded loss + no orphan-rows.

Run: .venv/bin/python tests/test_scribe_walker_robustness.py
"""
from __future__ import annotations
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_walker_robust_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.walker import recover_project   # noqa: E402

PROOT = Path(_tmp) / "projects"


def _bare_project_dir(pid: str) -> Path:
    """Minimal sidecar set: project.json + entities/ + (caller adds more)."""
    pdir = PROOT / pid
    (pdir / "entities").mkdir(parents=True, exist_ok=True)
    pdir.joinpath("project.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "pid": pid, "aba_commit": "src", "aba_version": "0.1",
        "source_project_dir": str(pdir),
        "registry": {"id": pid, "name": "test"},
    }))
    return pdir


def _add_entity_sidecar(pdir: Path, eid: str, *, type_: str = "analysis",
                       title: str = "T", status: str = "active") -> None:
    pdir.joinpath("entities", f"{eid}.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "id": eid, "type": type_, "title": title, "status": status,
        "created_at": "2026-06-08T00:00:00Z",
        "updated_at": "2026-06-08T00:00:00Z",
    }))


# ─── unit tests ─────────────────────────────────────────────────────────────
def test_torn_trailing_jsonl_line_is_skipped():
    pdir = _bare_project_dir("prj_tornJSONL")
    _add_entity_sidecar(pdir, "ana_a", title="A")
    _add_entity_sidecar(pdir, "ana_b", title="B")
    # edges.jsonl with one valid line + one torn line
    log = pdir / "edges.jsonl"
    log.write_text(
        '{"_v":1,"op":"add","src":"ana_a","dst":"ana_b","rel":"used","seq":1,"ts":"t"}\n'
        '{"_v":1,"op":"add","src":"ana_a","dst":"ana_b","rel":"u'   # truncated
    )
    rep = recover_project(pdir)
    # 1 valid edge applied; the torn line is silently dropped, not counted
    # (`edge_lines_skipped` covers no-op rows; a json.JSONDecodeError is in
    # _iter_jsonl which `continue`s without incrementing).
    assert rep.edges_applied == 1, f"expected 1 valid edge, got {rep.edges_applied}"


def test_corrupted_entity_sidecar_skipped():
    pdir = _bare_project_dir("prj_corruptEnt")
    _add_entity_sidecar(pdir, "ana_ok", title="OK")
    # A truncated / invalid JSON sidecar
    pdir.joinpath("entities", "ana_bad.json").write_text('{"id": "ana_bad",')
    rep = recover_project(pdir)
    assert any("unparseable entity sidecar" in w for w in rep.warnings), \
        f"expected warning, got: {rep.warnings}"
    # Live DB has only the good one (plus workspace from init_db bootstrap)
    db = sqlite3.connect(rep.target_db)
    n = db.execute("SELECT COUNT(*) FROM entities WHERE id LIKE 'ana_%'").fetchone()[0]
    db.close()
    assert n == 1


def test_edge_with_missing_endpoint_still_lands():
    """Walker doesn't enforce referential integrity — an edge whose endpoint
    has no sidecar is still inserted (the UI / agent will see a dangling
    reference, which is the same as during normal operation when an edge
    is mid-flight)."""
    pdir = _bare_project_dir("prj_orphanEdge")
    _add_entity_sidecar(pdir, "ana_a", title="A")
    # No sidecar for ana_ghost
    log = pdir / "edges.jsonl"
    log.write_text('{"_v":1,"op":"add","src":"ana_a","dst":"ana_ghost","rel":"used","seq":1,"ts":"t"}\n')
    rep = recover_project(pdir)
    assert rep.edges_applied == 1
    db = sqlite3.connect(rep.target_db)
    edges = db.execute("SELECT source_id, target_id FROM entity_edges").fetchall()
    db.close()
    assert edges == [("ana_a", "ana_ghost")]


def test_clear_sentinel_deletes_prior_messages_in_thread():
    pdir = _bare_project_dir("prj_clearSent")
    (pdir / "threads").mkdir()
    # Two messages, then clear, then one more — recovery should end up with
    # just the post-clear message.
    log = pdir / "threads" / "thr_x.jsonl"
    log.write_text(
        '{"_v":1,"id":1,"entity_id":"workspace","thread_id":"thr_x","role":"user","content":"hi","ts":"t1"}\n'
        '{"_v":1,"id":2,"entity_id":"workspace","thread_id":"thr_x","role":"assistant","content":"yo","ts":"t2"}\n'
        '{"_v":1,"op":"clear","entity_id":"workspace","thread_id":"thr_x","ts":"t3"}\n'
        '{"_v":1,"id":3,"entity_id":"workspace","thread_id":"thr_x","role":"user","content":"again","ts":"t4"}\n'
    )
    rep = recover_project(pdir)
    db = sqlite3.connect(rep.target_db)
    rows = db.execute(
        "SELECT id FROM messages WHERE thread_id = ? ORDER BY id", ("thr_x",)
    ).fetchall()
    db.close()
    assert [r[0] for r in rows] == [3], f"expected only id=3 to survive clear, got {rows}"


def test_out_of_order_seq_still_converges():
    """A torn write + replay could produce out-of-order seq numbers. Recovery
    just iterates lines in file order — INSERT OR IGNORE + DELETE are
    idempotent so the final state matches whatever the net set should be."""
    pdir = _bare_project_dir("prj_oooSeq")
    _add_entity_sidecar(pdir, "ana_a", title="A")
    _add_entity_sidecar(pdir, "ana_b", title="B")
    # Add seq=2, then add+remove seq=1+3 — net: 1 edge present
    log = pdir / "edges.jsonl"
    log.write_text(
        '{"_v":1,"op":"add","src":"ana_a","dst":"ana_b","rel":"used","seq":2,"ts":"t2"}\n'
        '{"_v":1,"op":"add","src":"ana_a","dst":"ana_b","rel":"used","seq":1,"ts":"t1"}\n'
        '{"_v":1,"op":"remove","src":"ana_a","dst":"ana_b","rel":"used","seq":3,"ts":"t3"}\n'
        '{"_v":1,"op":"add","src":"ana_a","dst":"ana_b","rel":"used","seq":4,"ts":"t4"}\n'
    )
    rep = recover_project(pdir)
    db = sqlite3.connect(rep.target_db)
    n = db.execute("SELECT COUNT(*) FROM entity_edges").fetchone()[0]
    db.close()
    # Net effect: add → add (dup, ignored) → remove (deletes) → add → so 1 edge present
    assert n == 1, f"expected 1 edge after replay, got {n}"


def test_snapshot_plus_tail_idempotent_overlap():
    """Snapshot's `add` lines overlapping with the tail's later mutations are
    handled by INSERT OR IGNORE / DELETE — recovery converges to the tail's
    final state."""
    pdir = _bare_project_dir("prj_snapTail")
    _add_entity_sidecar(pdir, "ana_a", title="A")
    _add_entity_sidecar(pdir, "ana_b", title="B")
    _add_entity_sidecar(pdir, "ana_c", title="C")
    # Snapshot at seq=5 captures two edges
    snap = pdir / "edges-snapshot-5.jsonl"
    snap.write_text(
        '{"_v":1,"op":"add","src":"ana_a","dst":"ana_b","rel":"used","seq":5,"ts":"snap"}\n'
        '{"_v":1,"op":"add","src":"ana_a","dst":"ana_c","rel":"used","seq":5,"ts":"snap"}\n'
    )
    # Tail: removes one of them, adds a different one
    tail = pdir / "edges.jsonl"
    tail.write_text(
        '{"_v":1,"op":"remove","src":"ana_a","dst":"ana_b","rel":"used","seq":6,"ts":"t6"}\n'
        '{"_v":1,"op":"add","src":"ana_b","dst":"ana_c","rel":"used","seq":7,"ts":"t7"}\n'
    )
    rep = recover_project(pdir)
    db = sqlite3.connect(rep.target_db)
    edges = sorted(db.execute("SELECT source_id, target_id FROM entity_edges").fetchall())
    db.close()
    # Expected: a→c (from snapshot, no remove), b→c (from tail).
    # NOT a→b (removed in tail).
    assert edges == [("ana_a", "ana_c"), ("ana_b", "ana_c")], f"unexpected edge set: {edges}"


# ─── kill-smoke integration ─────────────────────────────────────────────────
WORKER = r"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, '/workspace/aba/backend')
from core.recovery.scribe import Scribe, set_scribe_override
sc = Scribe(tick_interval=0.05)  # fast tick
set_scribe_override(sc)
sc.start()

from core import projects
from core.graph.entities import create_entity

projects.init()
p = projects.create_project('KillSmoke')
projects.set_current(p['id'])
print(f'PID={p["id"]}', flush=True)
# Stream 200 entity creates; the parent will SIGKILL us partway.
for i in range(200):
    create_entity(entity_type='analysis', title=f'A-{i}', metadata={'i': i})
    if i % 10 == 0:
        time.sleep(0.01)   # give parent a window to kill
print('DONE', flush=True)
"""


def test_kill_during_mutations_then_recover():
    # Run worker in its own subprocess so we can SIGKILL it cleanly
    proc = subprocess.Popen(
        [sys.executable, "-c", WORKER],
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    pid = None
    deadline = time.time() + 5.0
    # Read until we get the PID line, then kill after a short window
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith("PID="):
            pid = line.strip().split("=", 1)[1]
            break
    assert pid, "worker never reported PID"
    # Let it run a bit, then SIGKILL — simulates power loss / OOM
    time.sleep(0.3)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=5)
    assert proc.returncode != 0, "worker exited cleanly — try a smaller delay"
    # Now recover
    pdir = PROOT / pid
    assert pdir.exists()
    # Wipe DB to force a real rebuild from sidecars
    (pdir / "project.db").unlink(missing_ok=True)
    report = recover_project(pdir)
    # We don't know exactly how many landed (it's the whole point of the
    # smoke). Assert: recovery succeeded, at least SOME entities landed,
    # every edge references known entities (no orphans).
    assert report.entities >= 5, f"expected ≥5 entities recovered, got {report.entities}"
    # Referential check
    db = sqlite3.connect(pdir / "project.db")
    eids = {r[0] for r in db.execute("SELECT id FROM entities").fetchall()}
    orphan = db.execute(
        "SELECT source_id, target_id FROM entity_edges "
        "WHERE source_id NOT IN (SELECT id FROM entities) "
        "   OR target_id NOT IN (SELECT id FROM entities)"
    ).fetchall()
    db.close()
    # Note: imported scratch always has workspace bootstrapped, but we expect
    # at least 5 analysis rows + workspace = 6
    assert len(eids) >= 6, f"expected ≥6 rows incl workspace, got {len(eids)}"
    # No orphan edges (this run produced no edges so trivially true; but
    # the check stays in place to catch future-regression).
    assert orphan == [], f"orphan edges found post-recover: {orphan}"


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
