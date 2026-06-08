"""P0 — Scribe writer unit tests.

Each test constructs a fresh Scribe (no background thread — we drive it
manually via flush()) against a tempdir runtime. Coverage:

- Per-writer round-trip: entity sidecar, edge log, message log, project.json.
- Coalescing semantics: same-tick entity/project rewrites collapse.
- Hard-delete unlinks the sidecar.
- Edge `seq` is monotonic per-pid and persists in `.scribe/state.json`.
- Messages by-thread split into separate jsonl files; `clear` writes a sentinel.
- Writer exceptions are logged, not raised (the tick must survive).
- Queue overflow drains synchronously and continues.

Run: .venv/bin/python tests/test_scribe_writers.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_scribe_p0_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "aba.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ABA_RECOVERY_DISABLED"] = "1"  # don't auto-start the singleton

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import (   # noqa: E402
    Scribe,
    EntityUpserted, EntityHardDeleted,
    EdgeOp,
    MessageAppended, MessagesCleared,
    ProjectMetaChanged,
)

PROOT = Path(_tmp) / "projects"


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ─── entity writer ──────────────────────────────────────────────────────────
def test_entity_sidecar_roundtrip():
    s = Scribe()
    s.enqueue(EntityUpserted(pid="prj_a", entity_id="ana_x", row={
        "id": "ana_x", "type": "analysis", "title": "Quick QC",
        "metadata": '{"foo": 1}', "status": "active",
    }))
    s.flush()
    sidecar = PROOT / "prj_a" / "entities" / "ana_x.json"
    assert sidecar.exists(), "sidecar should be written"
    payload = json.loads(sidecar.read_text())
    assert payload["_v"] == 1
    assert payload["id"] == "ana_x"
    assert payload["title"] == "Quick QC"
    # metadata is a string of JSON in DB; sidecar should hold the decoded object
    assert payload["metadata"] == {"foo": 1}, "metadata should be normalized to JSON object"
    assert payload["_ts"]


def test_entity_hard_delete_unlinks():
    s = Scribe()
    s.enqueue(EntityUpserted(pid="prj_b", entity_id="ana_y", row={"id": "ana_y", "type": "analysis"}))
    s.flush()
    sidecar = PROOT / "prj_b" / "entities" / "ana_y.json"
    assert sidecar.exists()
    s.enqueue(EntityHardDeleted(pid="prj_b", entity_id="ana_y"))
    s.flush()
    assert not sidecar.exists(), "hard-delete should unlink the sidecar"


def test_entity_upsert_coalesces_same_tick():
    s = Scribe()
    # Three upserts to the same id in the same tick — only the last should land.
    for title in ("v1", "v2", "v3"):
        s.enqueue(EntityUpserted(pid="prj_c", entity_id="ana_z", row={"id": "ana_z", "title": title}))
    s.flush()
    sidecar = PROOT / "prj_c" / "entities" / "ana_z.json"
    assert json.loads(sidecar.read_text())["title"] == "v3"


# ─── edge writer ────────────────────────────────────────────────────────────
def test_edge_log_append_and_seq_monotone():
    s = Scribe()
    s.enqueue(EdgeOp(pid="prj_d", op="add", src="res_1", dst="fig_1", rel="includes"))
    s.enqueue(EdgeOp(pid="prj_d", op="add", src="res_1", dst="fig_2", rel="includes"))
    s.enqueue(EdgeOp(pid="prj_d", op="remove", src="res_1", dst="fig_1", rel="includes"))
    s.flush()
    log = PROOT / "prj_d" / "edges.jsonl"
    rows = _read_jsonl(log)
    assert len(rows) == 3
    assert [r["op"] for r in rows] == ["add", "add", "remove"]
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert [r["dst"] for r in rows] == ["fig_1", "fig_2", "fig_1"]


def test_edge_seq_persists_across_scribe_restart():
    # Run scribe #1: emit 2 events.
    s1 = Scribe()
    s1.enqueue(EdgeOp(pid="prj_e", op="add", src="a", dst="b", rel="r"))
    s1.enqueue(EdgeOp(pid="prj_e", op="add", src="a", dst="c", rel="r"))
    s1.flush()
    state = json.loads((PROOT / "prj_e" / ".scribe" / "state.json").read_text())
    assert state["last_edge_seq"] == 2

    # Run scribe #2 (fresh instance, simulating process restart): expect seq=3,4 next.
    s2 = Scribe()
    s2.enqueue(EdgeOp(pid="prj_e", op="add", src="a", dst="d", rel="r"))
    s2.enqueue(EdgeOp(pid="prj_e", op="remove", src="a", dst="b", rel="r"))
    s2.flush()
    rows = _read_jsonl(PROOT / "prj_e" / "edges.jsonl")
    assert [r["seq"] for r in rows] == [1, 2, 3, 4], "seq must continue monotonically"


def test_edge_seq_independent_per_project():
    s = Scribe()
    s.enqueue(EdgeOp(pid="prj_f1", op="add", src="a", dst="b", rel="r"))
    s.enqueue(EdgeOp(pid="prj_f2", op="add", src="x", dst="y", rel="r"))
    s.enqueue(EdgeOp(pid="prj_f1", op="add", src="a", dst="c", rel="r"))
    s.flush()
    r1 = _read_jsonl(PROOT / "prj_f1" / "edges.jsonl")
    r2 = _read_jsonl(PROOT / "prj_f2" / "edges.jsonl")
    assert [r["seq"] for r in r1] == [1, 2]
    assert [r["seq"] for r in r2] == [1]


# ─── message writer ─────────────────────────────────────────────────────────
def test_message_per_thread_split():
    s = Scribe()
    s.enqueue(MessageAppended(pid="prj_g", row={
        "id": 1, "role": "user", "content": [{"type": "text", "text": "hi"}],
        "thread_id": "thr_A", "ts": "2026-06-08T00:00:00Z",
    }))
    s.enqueue(MessageAppended(pid="prj_g", row={
        "id": 2, "role": "assistant", "content": [{"type": "text", "text": "yo"}],
        "thread_id": "thr_B", "ts": "2026-06-08T00:00:01Z",
    }))
    s.enqueue(MessageAppended(pid="prj_g", row={
        "id": 3, "role": "user", "content": [{"type": "text", "text": "?"}],
        "thread_id": None, "ts": "2026-06-08T00:00:02Z",
    }))
    s.flush()
    a = _read_jsonl(PROOT / "prj_g" / "threads" / "thr_A.jsonl")
    b = _read_jsonl(PROOT / "prj_g" / "threads" / "thr_B.jsonl")
    default = _read_jsonl(PROOT / "prj_g" / "threads" / "default.jsonl")
    assert len(a) == 1 and a[0]["id"] == 1
    assert len(b) == 1 and b[0]["id"] == 2
    assert len(default) == 1 and default[0]["id"] == 3


def test_messages_clear_writes_sentinel():
    s = Scribe()
    s.enqueue(MessageAppended(pid="prj_h", row={
        "id": 10, "role": "user", "content": [], "thread_id": "thr_X", "ts": "t1",
    }))
    s.enqueue(MessagesCleared(pid="prj_h", entity_id="workspace", thread_id="thr_X"))
    s.flush()
    rows = _read_jsonl(PROOT / "prj_h" / "threads" / "thr_X.jsonl")
    assert len(rows) == 2
    assert rows[1].get("op") == "clear"
    assert rows[1]["thread_id"] == "thr_X"


# ─── project meta writer ────────────────────────────────────────────────────
def test_project_meta_writes_fingerprint():
    s = Scribe()
    s.enqueue(ProjectMetaChanged(pid="prj_i", payload={
        "registry": {"display_name": "My project"},
        "project_entity": {"id": "prj_i", "type": "project", "title": "My project"},
    }))
    s.flush()
    pf = PROOT / "prj_i" / "project.json"
    payload = json.loads(pf.read_text())
    assert payload["_v"] == 1
    assert payload["pid"] == "prj_i"
    assert "aba_commit" in payload and payload["aba_commit"]   # at least 'unknown'
    assert "aba_version" in payload
    assert payload["registry"]["display_name"] == "My project"
    assert payload["project_entity"]["id"] == "prj_i"


def test_project_meta_coalesces_same_tick():
    s = Scribe()
    for name in ("first", "second", "final"):
        s.enqueue(ProjectMetaChanged(pid="prj_j", payload={"registry": {"display_name": name}}))
    s.flush()
    payload = json.loads((PROOT / "prj_j" / "project.json").read_text())
    assert payload["registry"]["display_name"] == "final"


# ─── robustness ─────────────────────────────────────────────────────────────
def test_unknown_event_does_not_crash():
    s = Scribe()
    s.enqueue("not-an-event")   # type: ignore[arg-type]
    s.flush()  # should not raise


def test_writer_exception_does_not_kill_tick(monkeypatch=None):
    s = Scribe()
    # Force the entity writer to fail; the edge write should still succeed.
    orig = s._write_entity
    def boom(pid, entity_id, row): raise RuntimeError("disk full")
    s._write_entity = boom  # type: ignore[assignment]
    s.enqueue(EntityUpserted(pid="prj_k", entity_id="ana_q", row={"id": "ana_q"}))
    s.enqueue(EdgeOp(pid="prj_k", op="add", src="a", dst="b", rel="r"))
    s.flush()
    assert (PROOT / "prj_k" / "edges.jsonl").exists(), "edge write should survive entity-writer failure"
    s._write_entity = orig  # type: ignore[assignment]


def test_overflow_synchronously_drains():
    # Tiny queue size + many events: overflow path triggers a synchronous drain.
    s = Scribe(max_queue_size=4)
    for i in range(20):
        s.enqueue(EdgeOp(pid="prj_l", op="add", src=f"s{i}", dst="d", rel="r"))
    s.flush()
    rows = _read_jsonl(PROOT / "prj_l" / "edges.jsonl")
    assert len(rows) == 20
    assert [r["seq"] for r in rows] == list(range(1, 21))


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
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
