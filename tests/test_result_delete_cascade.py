"""DELETE /api/entities/{result_id}?hard=true&cascade=members removes
the Result + its figure/table members + each member's revision chain
(active and superseded). A member that's ALSO referenced from outside
the cascade (e.g. included by a second Result) is preserved; the
inbound includes/supports/wasDerivedFrom edges from the deleted Result
are detached so the visible Result row disappears cleanly.

Without cascade=members the original blocking behavior stays in place
(refuses with 409 + a `references` list).

Run: .venv/bin/python tests/test_result_delete_cascade.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_del_cascade_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "d.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db        # noqa: E402
from core.graph import entities as _ent_mod   # noqa: E402
import content.bio                            # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


def _seed_figure(thread_id: str = "thr_del"):
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = ("import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
            "plt.figure();plt.plot([1,2,3],[1,4,9]);plt.savefig('s.png');plt.close('all')")
    res = run_python({"code": code}, ctx={"thread_id": thread_id,
                                          "tool_use_id": f"tu_{thread_id}"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=thread_id,
    )
    out = pin_artifact(res["exec_id"], "figure", 0,
                       wrap_in_result=False, thread_id=thread_id)
    return out["entity_id"]


def _revise(eid: str, y: float):
    from content.bio.lifecycle.revisions import make_revision
    code = (f"import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
            f"plt.figure();plt.plot([1,2,3],[{y},{y+1},{y+2}]);plt.savefig('r.png');plt.close('all')")
    return make_revision(eid, code)["new_entity_id"]


def test_cascade_deletes_result_plus_member_plus_chain():
    print("\n[1] cascade=members → Result + figure member + its revisions all gone")
    init_db()
    client = _client()

    anchor = _seed_figure("thr_del_a")
    rev2 = _revise(anchor, 5.0)
    rev3 = _revise(rev2, 9.0)
    # Wrap anchor in a Result
    r = client.post("/api/results", json={
        "thread_id": "thr_del_a", "title": "Cascade test",
        "members": [{"kind": "figure", "ref": anchor}],
    })
    check("create_result 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
    rid = r.json()["id"]
    # Sanity: chain is len 3
    chain = client.get(f"/api/entities/{anchor}/revisions").json()["chain"]
    check("chain has 3 entries before delete", len(chain) == 3,
          f"got {[e['id'] for e in chain]}")

    # Plain hard-delete (no cascade): should still REFUSE (current behavior)
    r = client.delete(f"/api/entities/{rid}?hard=true")
    check("non-cascade hard-delete refused", r.status_code == 409,
          f"got {r.status_code}: {r.text[:160]}")
    refs = r.json().get("detail", {}).get("references") or []
    check("refusal lists the figure as a reference", any(b["id"] == anchor for b in refs),
          f"refs={refs}")

    # cascade=members: should succeed
    r = client.delete(f"/api/entities/{rid}?hard=true&cascade=members")
    check("cascade hard-delete 200", r.status_code == 200,
          f"got {r.status_code}: {r.text[:200]}")
    body = r.json()
    deleted = {x["id"] for x in body.get("cascade_deleted") or []}
    check("anchor in cascade_deleted", anchor in deleted, f"got {deleted}")
    check("rev2 in cascade_deleted", rev2 in deleted, f"got {deleted}")
    check("rev3 in cascade_deleted", rev3 in deleted, f"got {deleted}")
    check("no skipped members", (body.get("skipped") or []) == [],
          f"got {body.get('skipped')}")

    # All four entities are gone (Result + 3 figures)
    check("Result entity gone", _ent_mod.get_entity(rid) is None)
    check("anchor figure gone", _ent_mod.get_entity(anchor) is None)
    check("rev2 figure gone", _ent_mod.get_entity(rev2) is None)
    check("rev3 figure gone", _ent_mod.get_entity(rev3) is None)


def test_cascade_preserves_member_shared_with_another_result():
    print("\n[2] member shared with another Result → preserved; only edges detached")
    init_db()
    client = _client()

    # Shared figure: pinned once, included in TWO results
    shared = _seed_figure("thr_del_b")
    private = _seed_figure("thr_del_b_priv")

    r1 = client.post("/api/results", json={
        "thread_id": "thr_del_b", "title": "Result with shared",
        "members": [
            {"kind": "figure", "ref": shared},
            {"kind": "figure", "ref": private},
        ],
    })
    rid1 = r1.json()["id"]
    r2 = client.post("/api/results", json={
        "thread_id": "thr_del_b", "title": "Other Result (also uses shared)",
        "members": [{"kind": "figure", "ref": shared}],
    })
    rid2 = r2.json()["id"]

    # Delete r1 with cascade — `shared` is also in r2, must be skipped
    # `private` is only in r1, gets cascade-deleted
    r = client.delete(f"/api/entities/{rid1}?hard=true&cascade=members")
    check("cascade hard-delete on r1 → 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    body = r.json()
    deleted_ids = {x["id"] for x in body.get("cascade_deleted") or []}
    skipped_ids = {x["id"] for x in body.get("skipped") or []}
    check("private figure cascade-deleted", private in deleted_ids,
          f"deleted={deleted_ids}")
    check("shared figure SKIPPED (kept)", shared in skipped_ids,
          f"skipped={skipped_ids}")
    check("Result r1 gone", _ent_mod.get_entity(rid1) is None)
    check("Result r2 still exists", _ent_mod.get_entity(rid2) is not None)
    check("shared figure still exists", _ent_mod.get_entity(shared) is not None)
    check("private figure gone", _ent_mod.get_entity(private) is None)

    # The Result→shared edges from r1 should be detached so r1's
    # presence in the graph really is gone (no lingering edges).
    from core.graph.edges import edges_from
    r1_edges = [e for e in edges_from(rid1) if e["target_id"] == shared]
    check("no lingering r1→shared edges", r1_edges == [], f"edges_from(r1)→shared = {r1_edges}")

    # r2 still has its includes edge to shared
    r2_edges = [e for e in edges_from(rid2) if e["target_id"] == shared]
    check("r2→shared edges intact", len(r2_edges) >= 1, f"got {r2_edges}")


def test_cascade_with_only_provenance_edges_does_not_block():
    print("\n[3] member with ONLY provenance (wasGeneratedBy) inbound is cascade-deleted, not skipped")
    # The harvester writes a `figure --wasGeneratedBy--> analysis` edge
    # on every pin. That's bookkeeping, NOT a dependency on the figure
    # by the analysis — the figure being deleted doesn't break the run.
    # The cascade keep-decision should only consider INBOUND dependency-
    # forming edges (includes/supports/wasDerivedFrom/wasRevisionOf).
    init_db()
    client = _client()
    fig = _seed_figure("thr_del_c")
    r = client.post("/api/results", json={
        "thread_id": "thr_del_c", "title": "Provenance-only test",
        "members": [{"kind": "figure", "ref": fig}],
    })
    rid = r.json()["id"]
    r = client.delete(f"/api/entities/{rid}?hard=true&cascade=members")
    check("cascade delete 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    body = r.json()
    skipped = body.get("skipped") or []
    check("provenance-only member NOT skipped", not any(s["id"] == fig for s in skipped),
          f"got skipped={skipped}")
    deleted_ids = {x["id"] for x in body.get("cascade_deleted") or []}
    check("provenance-only member cascade-deleted", fig in deleted_ids,
          f"got deleted={deleted_ids}")
    check("figure entity gone", _ent_mod.get_entity(fig) is None)


def main() -> int:
    test_cascade_deletes_result_plus_member_plus_chain()
    test_cascade_preserves_member_shared_with_another_result()
    test_cascade_with_only_provenance_edges_does_not_block()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL RESULT-DELETE-CASCADE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
