"""Hard-delete semantics (DELETE /api/entities/{id}?hard=true[&force=true]).

Every rel in the graph points source --rel--> target = "source builds on
target" (run --used--> dataset, result --includes--> member, artifact
--wasGeneratedBy--> run, rev2 --wasRevisionOf--> rev1, claim --supports-->
result). The delete guard is directional and rel-aware:

  * BLOCK (409): only live, non-cascade entities that DEPEND on the deletee —
    inbound edges whose rel is dependency-forming (_DEP_RELS: includes /
    supports / wasDerivedFrom / wasRevisionOf). The 409 carries
    `references` + `can_override: true`.
  * NEVER block: outbound edges (things the deletee builds on) and
    inbound bookkeeping stamps (used / wasGeneratedBy / produced_by).
  * OVERRIDE: `force=true` deletes despite dependents (informed consent —
    the UI shows the list first).
  * HONESTY: every surviving, non-archived neighbor of a hard-deleted
    entity gets a `severed_refs` metadata stamp naming what vanished
    ({id, type, title, at, rel, dir}) — a severed edge must not vanish
    in silence.

Before this contract the loop treated EVERY edge, both directions, all
rels, as a "live reference": a pinned artifact could never be hard-deleted
while its producing run existed, and a result whose wasDerivedFrom pointed
at its parent run 409'd even WITH cascade=members (the UI's own flow).

Drives main.entities_delete directly (like test_run_purge_on_delete) so no
server startup is needed. Each unlock case asserts its edge existed BEFORE
the delete (armed: an edge that failed to be written must fail the test,
not vacuously pass); each blocking case asserts the 409 fired AND the
entity survived; ceilings assert the blocker list contains ONLY genuine
dependents (the other side of the widening).

Run: python tests/test_delete_blockers.py   (or pytest)
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_del_blockers_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402  (heavy import: builds the FastAPI app, no server start)
from fastapi import HTTPException  # noqa: E402
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import (  # noqa: E402
    create_entity, get_entity, update_entity,
)
from core.graph.edges import add_edge, edges_from, edges_to  # noqa: E402
from core.config import project_data_dir  # noqa: E402
from core.projects import current_project_id  # noqa: E402

init_db()  # startup hook doesn't fire on import — create the schema ourselves


def _mk(entity_type: str, title: str, **kw) -> str:
    if entity_type in ("figure", "table") and "artifact_path" not in kw:
        kw["artifact_path"] = "/artifacts/test/x.png"   # registry-required
    out = create_entity(entity_type=entity_type, title=title, **kw)
    return out if isinstance(out, str) else out["id"]


def _delete(eid: str, cascade: str | None = None, force: bool = False):
    return main.entities_delete(entity_id=eid, hard=True, cascade=cascade,
                                force=force, _pid="test")


def _expect_409(eid: str, cascade: str | None = None) -> dict:
    """Delete must REFUSE: 409, non-empty references, can_override flag,
    and the entity survives. Returns the detail for shape assertions."""
    try:
        _delete(eid, cascade=cascade)
    except HTTPException as e:
        assert e.status_code == 409, f"expected 409, got {e.status_code}"
        detail = e.detail or {}
        assert detail.get("references"), f"409 must carry references: {detail!r}"
        assert detail.get("can_override") is True, \
            f"409 must advertise the force override: {detail!r}"
        assert get_entity(eid) is not None, "refused delete must not remove the entity"
        return detail
    raise AssertionError("delete succeeded but a 409 was expected")


def _severed(eid: str) -> list[dict]:
    e = get_entity(eid)
    assert e is not None
    return (e.get("metadata") or {}).get("severed_refs") or []


# ── unlocks: bookkeeping / outbound edges must NOT block ────────────────────

def test_outbound_provenance_stamp_never_blocks():
    """artifact --wasGeneratedBy--> live run: nothing depends on the
    artifact, so hard delete succeeds and the run survives (stamped)."""
    fig = _mk("figure", "Fig leaf")
    run = _mk("analysis", "Producing run")
    add_edge(fig, run, "wasGeneratedBy")
    assert any(e["rel_type"] == "wasGeneratedBy" for e in edges_from(fig))  # armed
    out = _delete(fig)
    assert out.get("ok") is True
    assert get_entity(fig) is None
    assert get_entity(run) is not None
    stamps = _severed(run)
    assert any(s["id"] == fig and s["rel"] == "wasGeneratedBy" for s in stamps), stamps


def test_inbound_consumption_blocks_and_spares_the_bytes():
    """run --used--> dataset: a live run CONSUMED this dataset, so deleting
    it destroys that run's inputs — and the delete route removes the bytes
    off disk in the same request. Unlike the lineage stamps, the target of a
    `used` edge is not reconstructible, so it refuses with the informed 409
    instead of erasing silently. The artifact must still be on disk after."""
    run = _mk("analysis", "Consumer run")
    data_root = Path(project_data_dir(current_project_id()))
    data_root.mkdir(parents=True, exist_ok=True)
    payload = data_root / "consumed_input"
    payload.mkdir(exist_ok=True)
    (payload / "data.parquet").write_text("x")
    ds = _mk("dataset", "Input data", artifact_path=str(payload))
    add_edge(run, ds, "used")
    assert any(e["rel_type"] == "used" for e in edges_to(ds))  # armed
    detail = _expect_409(ds)
    assert any(r["id"] == run for r in detail["references"]), detail
    assert payload.exists(), "a refused delete must not remove the bytes"


def test_inbound_consumption_force_overrides():
    """The override stays available: ?force deletes despite the consuming
    run, which survives with the severed stamp (informed, not blocked)."""
    run = _mk("analysis", "Consumer run 2")
    ds = _mk("dataset", "Input data 2")
    add_edge(run, ds, "used")
    out = _delete(ds, force=True)
    assert out.get("ok") is True
    assert get_entity(ds) is None
    assert get_entity(run) is not None
    assert any(s["id"] == ds and s["rel"] == "used" for s in _severed(run))


def test_inbound_generation_stamp_still_never_blocks():
    """WIDE / the other side: making `used` a blocker must not promote the
    OTHER bookkeeping stamps. A run's own output pointing back at it
    (wasGeneratedBy) records history — the run stays deletable."""
    run = _mk("analysis", "Producing run 2")
    fig = _mk("figure", "Its output")
    add_edge(fig, run, "wasGeneratedBy")
    assert any(e["rel_type"] == "wasGeneratedBy" for e in edges_to(run))  # armed
    out = _delete(run)
    assert out.get("ok") is True
    assert get_entity(run) is None
    assert get_entity(fig) is not None


def test_run_deletable_amid_stamps():
    """A run with inbound wasGeneratedBy (its artifacts) and outbound used
    (its inputs) is deletable; artifacts and inputs survive, each stamped,
    and the severed edges are actually swept from the graph."""
    run = _mk("analysis", "Old exploratory run")
    fig = _mk("figure", "Kept output")
    ds = _mk("dataset", "Kept input")
    add_edge(fig, run, "wasGeneratedBy")
    add_edge(run, ds, "used")
    assert edges_to(run) and edges_from(run)  # armed: both directions present
    out = _delete(run)
    assert out.get("ok") is True
    assert get_entity(run) is None
    assert get_entity(fig) is not None and get_entity(ds) is not None
    assert any(s["id"] == run for s in _severed(fig))
    assert any(s["id"] == run for s in _severed(ds))
    assert not edges_from(fig) and not edges_to(ds)  # edges swept, not dangling


def test_synthesis_outbound_refs_never_block():
    """claim --supports/wasDerivedFrom--> result: the claim builds on the
    result, not vice versa — retracting the claim must be free."""
    res = _mk("result", "Evidence result")
    claim = _mk("claim", "Bold claim")
    add_edge(claim, res, "supports")
    add_edge(claim, res, "wasDerivedFrom")
    assert len(edges_from(claim)) == 2  # armed
    out = _delete(claim)
    assert out.get("ok") is True
    assert get_entity(claim) is None
    assert get_entity(res) is not None


def test_result_delete_not_blocked_by_parent_run():
    """result --wasDerivedFrom--> live parent run (the promote flow writes
    this) must not block — with OR without cascade=members. This was the
    production dead-end: the UI always sends cascade=members for results,
    and the parent run is never in the cascade set."""
    run = _mk("analysis", "Parent run")
    r1 = _mk("result", "Result via cascade")
    add_edge(r1, run, "wasDerivedFrom")
    assert any(e["rel_type"] == "wasDerivedFrom" for e in edges_from(r1))  # armed
    out = _delete(r1, cascade="members")
    assert out.get("ok") is True
    assert get_entity(r1) is None

    r2 = _mk("result", "Result no cascade")
    add_edge(r2, run, "wasDerivedFrom")
    out = _delete(r2)  # degenerate shape: cascade param absent entirely
    assert out.get("ok") is True
    assert get_entity(r2) is None
    assert get_entity(run) is not None


# ── blocks + override: genuine dependents ────────────────────────────────────

def test_inbound_dependent_blocks_without_force():
    """result --includes--> figure: a live container depends on its member;
    deleting the member refuses (409 + can_override) and names ONLY the
    genuine dependent — bookkeeping neighbors must not pad the list."""
    res = _mk("result", "Container result")
    fig = _mk("figure", "Included figure")
    run = _mk("analysis", "Noise producer")
    add_edge(res, fig, "includes")
    add_edge(fig, run, "wasGeneratedBy")     # outbound noise
    detail = _expect_409(fig)
    refs = detail["references"]
    assert any(b["id"] == res and b["rel_type"] == "includes" for b in refs), refs
    assert all(b["id"] != run for b in refs), \
        f"non-dependent neighbors must not appear as blockers: {refs}"
    assert get_entity(res) is not None


def test_inbound_revision_blocks():
    """rev2 --wasRevisionOf--> rev1: a live revision depends on its parent;
    deleting the parent refuses (chain surgery is delete_revision's job)."""
    rev1 = _mk("figure", "Rev 1")
    rev2 = _mk("figure", "Rev 2")
    add_edge(rev2, rev1, "wasRevisionOf")
    refs = _expect_409(rev1)["references"]
    assert any(b["id"] == rev2 and b["rel_type"] == "wasRevisionOf" for b in refs), refs


def test_force_overrides_and_stamps_dependents():
    """force=true deletes despite a live dependent; the dependent survives,
    its edge is swept, and it carries the severed_refs stamp."""
    res = _mk("result", "Attached result")
    fig = _mk("figure", "Force-deleted figure")
    add_edge(res, fig, "includes")
    _expect_409(fig)                          # armed: the guard actually fires
    out = _delete(fig, force=True)
    assert out.get("ok") is True
    assert get_entity(fig) is None
    assert get_entity(res) is not None
    stamps = _severed(res)
    assert any(s["id"] == fig and s["rel"] == "includes" for s in stamps), stamps
    st = next(s for s in stamps if s["id"] == fig)
    assert st.get("type") == "figure" and st.get("title") and st.get("at"), st
    assert not [e for e in edges_from(res) if e["target_id"] == fig]  # swept


# ── degenerate shapes ────────────────────────────────────────────────────────

def test_archived_dependent_does_not_block_or_get_stamped():
    """An ARCHIVED container is already gone from the user's view — its
    includes edge neither blocks nor earns a severed_refs stamp."""
    res = _mk("result", "Archived container")
    fig = _mk("figure", "Orphanable figure")
    add_edge(res, fig, "includes")
    update_entity(res, status="archived")
    assert any(e["rel_type"] == "includes" for e in edges_to(fig))  # armed
    out = _delete(fig)
    assert out.get("ok") is True
    assert get_entity(fig) is None
    assert _severed(res) == []


def test_edgeless_entity_deletes_clean():
    """No edges at all (the common note/scratch shape) — hard delete works
    and nothing anywhere gets stamped."""
    note = _mk("note", "Loose note")
    assert not edges_to(note) and not edges_from(note)
    out = _delete(note)
    assert out.get("ok") is True
    assert get_entity(note) is None


def test_force_without_blockers_is_noop_flag():
    """force=true on an unblocked delete behaves exactly like force=false
    (the flag widens, never narrows)."""
    note = _mk("note", "Free note")
    out = _delete(note, force=True)
    assert out.get("ok") is True
    assert get_entity(note) is None


def test_preserved_cascade_member_records_the_severed_edge():
    """A member kept out of the cascade (it's referenced from outside) has its
    Result→member edge cut — the ONE case where an edge vanishes and the
    entity survives. The survivor must carry the same severed_refs stamp every
    other survivor gets; otherwise the honesty record has a hole exactly where
    the graph changed under the user."""
    res = _mk("result", "Result being deleted")
    other = _mk("result", "Another result")
    fig = _mk("figure", "Shared figure")
    # cascade membership comes from metadata.members (the bio service), while
    # the blocker/keep decision reads the edges — the fixture needs both.
    update_entity(res, metadata={"members": [{"kind": "figure", "ref": fig}]})
    add_edge(res, fig, "includes")
    add_edge(other, fig, "includes")            # the outside reference
    out = _delete(res, cascade="members", force=True)
    assert get_entity(fig) is not None, "the shared member must be preserved"
    assert any(s["id"] == res for s in _severed(fig)), \
        f"a preserved member must record the cut edge: {_severed(fig)}"
    assert any(s["id"] == fig for s in out.get("skipped", [])), out


_TESTS = [
    test_outbound_provenance_stamp_never_blocks,
    test_inbound_consumption_blocks_and_spares_the_bytes,
    test_inbound_consumption_force_overrides,
    test_inbound_generation_stamp_still_never_blocks,
    test_run_deletable_amid_stamps,
    test_synthesis_outbound_refs_never_block,
    test_result_delete_not_blocked_by_parent_run,
    test_inbound_dependent_blocks_without_force,
    test_inbound_revision_blocks,
    test_force_overrides_and_stamps_dependents,
    test_archived_dependent_does_not_block_or_get_stamped,
    test_edgeless_entity_deletes_clean,
    test_force_without_blockers_is_noop_flag,
    test_preserved_cascade_member_records_the_severed_edge,
]


def _standalone() -> int:
    import traceback
    rc = 0
    for t in _TESTS:
        try:
            t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
