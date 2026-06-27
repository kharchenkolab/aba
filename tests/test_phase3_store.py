"""Phase 3, 3.1 — the find_entities typed read API (the store's read surface)."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_phase3_31_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"), "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "a"), "ABA_WORK_DIR": str(Path(_tmp) / "w"),
    "DATA_DIR": str(Path(_tmp) / "d"),
})
sys.path.insert(0, str(ROOT / "backend"))
from core.graph._schema import init_db   # noqa: E402
init_db()
from core.graph.entities import create_entity, find_entities, exists_entity   # noqa: E402
from core.graph.derivation import manual   # noqa: E402


def _mk(t, title, **kw):
    return create_entity(entity_type=t, title=title, derivation=manual(), **kw)


def test_type_and_status_predicates():
    a, b = _mk("p_note", "n1"), _mk("p_note", "n2")
    assert {e["id"] for e in find_entities(type="p_note")} == {a, b}
    assert {e["id"] for e in find_entities(type_in=["p_note"])} == {a, b}
    assert all(e["status"] == "active" for e in find_entities(type="p_note", status="active"))


def test_parent_and_exists():
    p = _mk("p_run", "run1")
    ch = _mk("p_fig", "ch", artifact_path="/tmp/c.png", parent_entity_id=p)
    assert {e["id"] for e in find_entities(parent_entity_id=p)} == {ch}
    assert exists_entity(parent_entity_id=p)
    assert not exists_entity(parent_entity_id="nope")


def test_exec_artifact_predicates():
    e = _mk("p_fig", "fx", artifact_path="/tmp/x.png", exec_id="ex9",
            artifact_kind="figure", artifact_idx=0)
    got = find_entities(exec_id="ex9", artifact_kind="figure", artifact_idx=0)
    assert [x["id"] for x in got] == [e]


def test_metadata_contains():
    e = _mk("p_note", "kept", metadata={"source_key": "sk1"})
    assert [x["id"] for x in find_entities(type="p_note", metadata_contains={"source_key": "sk1"})] == [e]
    assert find_entities(type="p_note", metadata_contains={"source_key": "nope"}) == []


def test_text_query_title_or_notes():
    from core.graph.entities import update_entity
    e = _mk("p_note", "alpha thing"); update_entity(e, notes="mentions zebra")
    assert e in {x["id"] for x in find_entities(type="p_note", text_query="zebra")}
    assert e in {x["id"] for x in find_entities(type="p_note", text_query="alpha")}


def test_title_exact_descending_limit():
    _mk("p_fig", "samename", artifact_path="/tmp/1.png")
    f2 = _mk("p_fig", "samename", artifact_path="/tmp/2.png")
    got = find_entities(type="p_fig", title="samename", descending=True, limit=1)
    assert len(got) == 1 and got[0]["id"] == f2   # newest first


def test_store_swap_bio_reads_decoupled(monkeypatch):
    """§8 swap-readiness: bio read sites go through the store API (find_entities /
    exists_entity), NOT raw SQL — so swapping the store impl wouldn't touch bio.
    Proven by swapping find_entities with an in-memory implementation and showing a
    bio read site (search.find_kept_note) works against it."""
    import core.graph.entities as E
    _mem = [{"id": "n1", "type": "note", "metadata": {"source_key": "sk1"}}]

    def _mem_find(**pred):
        rows = [r for r in _mem if pred.get("type") in (None, r["type"])]
        mc = pred.get("metadata_contains") or {}
        rows = [r for r in rows if all((r.get("metadata") or {}).get(k) == v for k, v in mc.items())]
        return rows[:pred["limit"]] if pred.get("limit") else rows

    monkeypatch.setattr(E, "find_entities", _mem_find)
    from content.bio.graph.search import find_kept_note
    assert find_kept_note("sk1") == "n1"     # bio read served by the in-memory store
    assert find_kept_note("nope") is None
