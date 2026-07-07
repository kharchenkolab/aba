"""Phase 2 write-path test: the in-kernel `aba` write verbs emit intents that the
backend's harvest_intents() executes with full context — create/update/relate,
with local-ref ('aba:new:N') resolution and provenance. Uses synthetic entity
types so edge validation passes through (no coupling to the bio registry).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _fresh_db():
    tmp = tempfile.mkdtemp(prefix="aba_writes_")
    dbp = str(Path(tmp) / "project.db")
    os.environ["ABA_DB_PATH"] = dbp
    os.environ["ABA_PROJECT_DB"] = dbp
    from core.graph._schema import init_db, set_db_path
    set_db_path(dbp)
    init_db()
    return dbp


def test_create_update_relate_roundtrip():
    dbp = _fresh_db()
    work = tempfile.mkdtemp(prefix="aba_work_")
    os.environ["WORK_DIR"] = work
    from core.exec.kernels.aba_inkernel import _Aba
    from core.exec.run import harvest_intents
    from core.graph.entities import get_entity
    from core.graph.edges import edges_from

    aba = _Aba(db=dbp)
    a = aba.create("t_input", "counts", metadata={"organism": "human"})    # local ref
    b = aba.create("t_output", "result table")
    aba.update(a, notes="primary")            # update via LOCAL REF (resolution)
    aba.relate(b, "t_derived_from", a)        # edge via LOCAL REFS (resolution)

    # nothing hit the graph yet — writes are deferred to harvest
    res = harvest_intents(work)

    creates = {r["title"]: r["id"] for r in res if r["verb"] == "create"}
    assert set(creates) == {"counts", "result table"}, res
    aid, bid = creates["counts"], creates["result table"]

    # create landed with the right type/title
    ea = get_entity(aid)
    assert ea and ea["type"] == "t_input" and ea["title"] == "counts"
    # provenance stamped backend-side (derivation=manual)
    assert "manual" in str(ea.get("derivation")), ea.get("derivation")
    # update resolved the local ref and applied
    assert get_entity(aid).get("notes") == "primary"
    # relate resolved BOTH local refs → a real edge b -> a
    tgts = {e.get("target_id") or e.get("target") for e in edges_from(bid)}
    assert aid in tgts, (bid, aid, edges_from(bid))

    # the intents file is consumed, and a re-harvest is a clean no-op
    assert not (Path(work) / ".aba_intents.jsonl").exists()
    assert harvest_intents(work) == []


def test_create_parity_with_direct():
    dbp = _fresh_db()
    work = tempfile.mkdtemp(prefix="aba_work_")
    os.environ["WORK_DIR"] = work
    from core.exec.kernels.aba_inkernel import _Aba
    from core.exec.run import harvest_intents
    from core.graph.entities import get_entity, create_entity
    from core.graph.derivation import manual

    # intent-created vs direct create_entity → same shape
    aba = _Aba(db=dbp)
    aba.create("t_thing", "via intent")
    res = harvest_intents(work)
    via_intent = get_entity(res[0]["id"])
    direct = get_entity(create_entity(entity_type="t_thing", title="via direct", derivation=manual()))
    assert via_intent["type"] == direct["type"] == "t_thing"
    assert via_intent["status"] == direct["status"]
    assert "manual" in str(via_intent.get("derivation")) and "manual" in str(direct.get("derivation"))


def test_lifecycle_verb_via_bio_service():
    # Content-provided lifecycle verb (aba.promote) → dispatched through the aba_intent
    # service to promote_to_result_tool, with local-ref + provenance. Proves the whole
    # contact plane flips coherently (core domain-neutral; bio contributes the verb).
    dbp = _fresh_db()
    work = tempfile.mkdtemp(prefix="aba_lc_")
    os.environ["WORK_DIR"] = work
    import content.bio  # noqa: F401  loads types
    import content.bio.services  # noqa: F401  registers aba_intent + aba_kernel_verbs
    from core.graph.entities import create_entity, find_entities
    from core.graph.derivation import manual
    from core.exec.kernels.aba_inkernel import _Aba
    from core.exec.run import harvest_intents
    from content.bio.services import _aba_kernel_verbs
    fig = create_entity(entity_type="figure", title="F", derivation=manual(),
                        artifact_path=work + "/x.png")
    aba = _Aba(db=os.environ["ABA_PROJECT_DB"])
    ns = {"aba": aba}
    exec(_aba_kernel_verbs(), ns)          # bio attaches promote/finding/claim/register_dataset
    assert hasattr(aba, "promote") and hasattr(aba, "finding")
    aba.promote(fig, "the interpretation", title="R1")
    res = harvest_intents(work, ctx=None)
    promoted = [r for r in res if r.get("verb") == "promote"]
    assert promoted and promoted[0].get("id"), res
    assert "R1" in [e["title"] for e in find_entities(type="result")]


def test_archive_intent():
    _fresh_db()
    work = tempfile.mkdtemp(prefix="aba_arch_")
    os.environ["WORK_DIR"] = work
    from core.graph.entities import create_entity, get_entity
    from core.graph.derivation import manual
    from core.exec.kernels.aba_inkernel import _Aba
    from core.exec.run import harvest_intents
    e = create_entity(entity_type="dataset", title="d", derivation=manual())
    aba = _Aba(db=os.environ["ABA_PROJECT_DB"])
    aba.archive(e)
    res = harvest_intents(work)
    assert res == [{"verb": "archive", "id": e}], res
    assert get_entity(e)["status"] == "archived"


def test_no_intents_is_noop():
    _fresh_db()
    work = tempfile.mkdtemp(prefix="aba_work_")
    from core.exec.run import harvest_intents
    assert harvest_intents(work) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all write-path tests passed")
