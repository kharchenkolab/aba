"""P2 — Integration: edge + message mutations flow through scribe.

Covers:
- add_edge writes a line to edges.jsonl with op="add" and monotonic seq
- duplicate add_edge (INSERT OR IGNORE) does NOT emit a second line
- remove_edge writes op="remove"; no-op remove emits nothing
- append_message writes per-thread jsonl
- clear_messages writes a `clear` sentinel
- Cross-project seq counters stay independent
- Persistence: seq survives a "process restart" (new Scribe instance)

Run: .venv/bin/python tests/test_scribe_edge_msg_hooks.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_scribe_p2_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override  # noqa: E402

_scribe = Scribe(tick_interval=10_000.0)
set_scribe_override(_scribe)

from core import projects                                     # noqa: E402
from core.graph.entities import create_entity                 # noqa: E402
from core.graph.edges import add_edge, remove_edge            # noqa: E402
from core.graph.messages import append_message, clear_messages  # noqa: E402

projects.init()

PROOT = Path(_tmp) / "projects"


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ─── edge wiring ────────────────────────────────────────────────────────────
def test_add_edge_appends_to_edge_log():
    p = projects.create_project("Edges-A")
    pid = p["id"]
    projects.set_current(pid)
    # Two real entities so edge validation passes (allowed_edges checks).
    a = create_entity(entity_type="analysis", title="A")
    b = create_entity(entity_type="finding", title="B")  # finding from analysis is allowed
    _scribe.flush()
    add_edge(a, b, "supports")
    _scribe.flush()
    log = PROOT / pid / "edges.jsonl"
    rows = _read_jsonl(log)
    assert len(rows) == 1
    assert rows[0]["op"] == "add"
    assert rows[0]["src"] == a and rows[0]["dst"] == b
    assert rows[0]["rel"] == "supports"
    assert rows[0]["seq"] == 1


def test_add_edge_idempotent_does_not_double_emit():
    p = projects.create_project("Edges-B")
    pid = p["id"]
    projects.set_current(pid)
    a = create_entity(entity_type="analysis", title="A")
    b = create_entity(entity_type="finding", title="B")
    _scribe.flush()
    add_edge(a, b, "supports")
    add_edge(a, b, "supports")  # dup — INSERT OR IGNORE silently drops
    _scribe.flush()
    rows = _read_jsonl(PROOT / pid / "edges.jsonl")
    assert len(rows) == 1, f"expected one row (idempotent), got {len(rows)}"


def test_remove_edge_emits_only_when_it_removed():
    p = projects.create_project("Edges-C")
    pid = p["id"]
    projects.set_current(pid)
    a = create_entity(entity_type="analysis", title="A")
    b = create_entity(entity_type="finding", title="B")
    _scribe.flush()
    add_edge(a, b, "supports")
    _scribe.flush()
    # Real removal
    remove_edge(a, b, "supports")
    # No-op removal — already gone
    remove_edge(a, b, "supports")
    _scribe.flush()
    rows = _read_jsonl(PROOT / pid / "edges.jsonl")
    # add + remove = 2 lines (the no-op DELETE doesn't emit)
    assert len(rows) == 2
    assert rows[1]["op"] == "remove"


# ─── message wiring ─────────────────────────────────────────────────────────
def test_append_message_per_thread_split():
    p = projects.create_project("Msgs-A")
    pid = p["id"]
    projects.set_current(pid)
    append_message("user",   [{"type": "text", "text": "hi"}], thread_id="thr_X")
    append_message("agent",  [{"type": "text", "text": "yo"}], thread_id="thr_X")
    append_message("user",   [{"type": "text", "text": "hey"}], thread_id="thr_Y")
    _scribe.flush()
    x = _read_jsonl(PROOT / pid / "threads" / "thr_X.jsonl")
    y = _read_jsonl(PROOT / pid / "threads" / "thr_Y.jsonl")
    assert len(x) == 2 and x[0]["role"] == "user" and x[1]["role"] == "agent"
    assert len(y) == 1 and y[0]["role"] == "user"
    assert all(r["content"][0]["text"] for r in x + y)


def test_clear_messages_writes_sentinel():
    p = projects.create_project("Msgs-B")
    pid = p["id"]
    projects.set_current(pid)
    append_message("user", [{"type": "text", "text": "hi"}], thread_id="thr_Z")
    clear_messages("workspace")
    _scribe.flush()
    # The clear sentinel writes to the "default" thread file (thread_id None
    # in our hook). Confirm a clear-op line exists somewhere under threads/.
    threads_dir = PROOT / pid / "threads"
    found_clear = False
    for f in threads_dir.glob("*.jsonl"):
        for r in _read_jsonl(f):
            if r.get("op") == "clear":
                found_clear = True
    assert found_clear, "clear sentinel should be present in some thread file"


# ─── seq independence + persistence ─────────────────────────────────────────
def test_edge_seq_is_per_project():
    p1 = projects.create_project("Seq-1")
    pid1 = p1["id"]
    projects.set_current(pid1)
    a = create_entity(entity_type="analysis", title="A")
    b = create_entity(entity_type="finding", title="B")
    add_edge(a, b, "supports")
    p2 = projects.create_project("Seq-2")
    pid2 = p2["id"]
    projects.set_current(pid2)
    c = create_entity(entity_type="analysis", title="C")
    d = create_entity(entity_type="finding", title="D")
    add_edge(c, d, "supports")
    add_edge(c, d, "supports")  # idempotent — no second emit
    _scribe.flush()
    rows1 = _read_jsonl(PROOT / pid1 / "edges.jsonl")
    rows2 = _read_jsonl(PROOT / pid2 / "edges.jsonl")
    assert [r["seq"] for r in rows1] == [1]
    assert [r["seq"] for r in rows2] == [1]


def test_edge_seq_persists_across_scribe_restart():
    global _scribe
    p = projects.create_project("Seq-Persist")
    pid = p["id"]
    projects.set_current(pid)
    a = create_entity(entity_type="analysis", title="A")
    b = create_entity(entity_type="finding", title="B")
    add_edge(a, b, "supports")
    _scribe.flush()
    state = json.loads((PROOT / pid / ".scribe" / "state.json").read_text())
    assert state["last_edge_seq"] == 1

    # Swap in a fresh Scribe (simulating process restart)
    new_scribe = Scribe(tick_interval=10_000.0)
    set_scribe_override(new_scribe)
    _scribe = new_scribe

    # Re-use existing entities — type validation still passes.
    remove_edge(a, b, "supports")
    add_edge(a, b, "supports")
    _scribe.flush()
    rows = _read_jsonl(PROOT / pid / "edges.jsonl")
    # Pre-restart: [seq=1 add]. Post-restart: [seq=2 remove, seq=3 add].
    assert [r["seq"] for r in rows] == [1, 2, 3]


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
