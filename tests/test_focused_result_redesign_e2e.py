"""End-to-end integration test for the focused-Result redesign.

Exercises the exact HTTP surface the redesigned UI hits:
  - POST /api/results               → seed a Result with a pinned figure
  - GET  /api/entities/{id}/revisions  → what useFigureHistory polls
  - POST /api/entities/{id}/make_revision → both default + supersede_newer
  - figure_history(include_superseded=False) → linear chain for display

Walks the full UX:
  1. Pin a figure → wrap it in a Result with one member (anchor).
  2. Make revision #2 from latest (no supersede needed).
  3. Make revision #3 from latest (no supersede needed).
  4. /revisions returns the 3-entry chain newest-first.
  5. Attempt to revise from rev #1 WITHOUT supersede_newer →
     400, error body includes the newer ids (matches the UI
     confirmation-dialog trigger).
  6. Retry with supersede_newer=True → succeeds; revisions #2 and
     #3 are marked superseded; the visible chain is now [new, anchor]
     (length 2, not a 4-node branch).
  7. /revisions on the anchor returns ONLY the visible chain.

Run: .venv/bin/python tests/test_focused_result_redesign_e2e.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_redesign_e2e_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "rd.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
from core.graph import entities         # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _seed_figure(thread_id="thr_e2e"):
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[1,4,9])\n"
        "plt.savefig('seed.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": thread_id, "tool_use_id": "tu_seed"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=thread_id,
    )
    out = pin_artifact(res["exec_id"], "figure", 0,
                       wrap_in_result=False, thread_id=thread_id)
    return out["entity_id"]


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_full_lifecycle_through_http():
    print("\n[full lifecycle] pin → result(member) → revise×2 → revise-non-latest with supersede")
    init_db()
    client = _client()

    # 1. Seed and pin a figure (the anchor)
    anchor_id = _seed_figure(thread_id="thr_e2e_a")
    print(f"    anchor: {anchor_id}")

    # 2. Wrap the figure in a Result with one member (mimics the
    #    Group-into-result flow that ResultView is the focused view of)
    r = client.post("/api/results", json={
        "thread_id": "thr_e2e_a",
        "title": "Test result with revisable figure",
        "interpretation": "",
        "members": [{"kind": "figure", "ref": anchor_id}],
    })
    check("create_result 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
    result_id = r.json()["id"]
    members = r.json()["metadata"]["members"]
    check("result has 1 figure member", len(members) == 1 and members[0]["ref"] == anchor_id)

    # 3. /revisions on the anchor returns chain of 1 (just itself)
    r = client.get(f"/api/entities/{anchor_id}/revisions")
    check("revisions(anchor) 200", r.status_code == 200)
    body = r.json()
    check("chain has 1 entry initially", len(body["chain"]) == 1, f"got {[e['id'] for e in body['chain']]}")
    check("position is 0", body["position"] == 0)
    check("prev is None", body["prev"] is None)
    check("next is None", body["next"] is None)

    # 4. Make revision #2 from the latest (which is also the anchor — trivial)
    code_v2 = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[2,8,18])\n"
        "plt.savefig('v2.png'); plt.close('all')\n"
    )
    r = client.post(f"/api/entities/{anchor_id}/make_revision",
                    json={"modified_code": code_v2, "title": "rev 2"})
    check("make_revision #2 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
    body = r.json()
    rev2_id = body["entity"]["id"]
    check("rev2 returned with new id", isinstance(rev2_id, str))
    check("rev2.wasRevisionOf = anchor", body["wasRevisionOf"] == anchor_id)
    check("rev2.superseded is empty", body["superseded"] == [])

    # 5. Make revision #3 (from latest = rev2)
    code_v3 = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[3,12,27])\n"
        "plt.savefig('v3.png'); plt.close('all')\n"
    )
    r = client.post(f"/api/entities/{rev2_id}/make_revision",
                    json={"modified_code": code_v3, "title": "rev 3"})
    check("make_revision #3 200 (from latest)", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
    rev3_id = r.json()["entity"]["id"]
    check("rev3.wasRevisionOf = rev2", r.json()["wasRevisionOf"] == rev2_id)

    # 6. /revisions from anchor returns chain of 3, newest first
    #    What the UI's useFigureHistory(anchor.id) sees on load.
    r = client.get(f"/api/entities/{anchor_id}/revisions")
    check("revisions(anchor) returns chain of 3", len(r.json()["chain"]) == 3,
          f"got {[e['id'] for e in r.json()['chain']]}")
    chain_ids = [e["id"] for e in r.json()["chain"]]
    check("newest in chain is rev3", chain_ids[0] == rev3_id)
    check("middle is rev2",         chain_ids[1] == rev2_id)
    check("oldest is anchor",       chain_ids[2] == anchor_id)
    check("position(anchor) = 2",   r.json()["position"] == 2)

    # 7. UI default-to-latest: useFigureRevisions snaps displayedId to
    #    chain[0]. User then clicks back to the anchor (rev 1 of 3) and
    #    clicks Revise — the strip detects !isLatest and shows the
    #    confirmation dialog. UI sends the request WITHOUT
    #    supersede_newer first to surface the newer list to the user
    #    (the new make_revision_endpoint mirrors this behavior).
    code_v4 = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[5,15,30])\n"
        "plt.savefig('v4.png'); plt.close('all')\n"
    )
    r = client.post(f"/api/entities/{anchor_id}/make_revision",
                    json={"modified_code": code_v4, "title": "rev from anchor"})
    check("non-latest revise returns 400", r.status_code == 400, f"got {r.status_code}")
    check("400 detail names 'newer'", "newer" in r.text.lower(), f"got: {r.text[:180]}")
    check("400 includes rev2 id in newer", rev2_id in r.text, f"got: {r.text[:200]}")
    check("400 includes rev3 id in newer", rev3_id in r.text, f"got: {r.text[:200]}")

    # 8. User confirms the dialog → UI re-sends with supersede_newer=True
    r = client.post(f"/api/entities/{anchor_id}/make_revision",
                    json={"modified_code": code_v4, "title": "rev from anchor",
                          "supersede_newer": True})
    check("supersede_newer=True revise 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    body = r.json()
    rev4_id = body["entity"]["id"]
    superseded = set(body["superseded"])
    check("response.superseded includes rev2 + rev3", {rev2_id, rev3_id}.issubset(superseded),
          f"got {superseded}")

    # 9. Entity statuses: rev2 and rev3 → superseded; anchor + rev4 → active
    check("rev2 marked superseded",   (entities.get_entity(rev2_id) or {}).get("status") == "superseded")
    check("rev3 marked superseded",   (entities.get_entity(rev3_id) or {}).get("status") == "superseded")
    check("anchor remains active",    (entities.get_entity(anchor_id) or {}).get("status") == "active")
    check("rev4 is active",           (entities.get_entity(rev4_id) or {}).get("status") == "active")

    # 10. /revisions(anchor) returns the visible chain ONLY:
    #     [rev4 (newest active), anchor]. Length 2, NOT 4.
    r = client.get(f"/api/entities/{anchor_id}/revisions")
    visible = [e["id"] for e in r.json()["chain"]]
    check("visible chain has 2 entries (rev4 + anchor)", len(visible) == 2, f"got {visible}")
    check("visible newest is rev4", visible[0] == rev4_id)
    check("visible oldest is anchor", visible[1] == anchor_id)
    check("rev2 NOT in visible chain", rev2_id not in visible)
    check("rev3 NOT in visible chain", rev3_id not in visible)

    # 11. /revisions(rev2_superseded) — starting from a superseded
    #     entity (an admin entered the URL by hand, say), the chain
    #     walk filters superseded children, so it cannot reach the
    #     active rev4 (rev3 — the only forward edge — is also
    #     superseded). It correctly walks backwards to anchor.
    #     This is the expected branch-collapse semantic; the new UI's
    #     useFigureHistory always anchors on member.ref (active by
    #     construction) so it never hits this edge case.
    r = client.get(f"/api/entities/{rev2_id}/revisions")
    visible_from_rev2 = [e["id"] for e in r.json()["chain"]]
    check("from rev2: walk finds anchor (parent)",
          anchor_id in visible_from_rev2, f"got {visible_from_rev2}")
    check("from rev2: walk does NOT cross the superseded boundary to rev4",
          rev4_id not in visible_from_rev2,
          f"got {visible_from_rev2} — rev2's forward edge goes to rev3 (also superseded), so the walk halts there")


def test_admin_include_superseded_surfaces_all():
    print("\n[admin] figure_history(include_superseded=True) surfaces displaced revisions")
    from content.bio.graph.figure_history import figure_history
    init_db()
    anchor = _seed_figure(thread_id="thr_e2e_b")
    client = _client()

    def _post_revise(parent_id, code_snippet, label, supersede=False):
        body = {
            "modified_code":
                f"import matplotlib; matplotlib.use('Agg'); "
                f"import matplotlib.pyplot as plt; "
                f"plt.figure(); plt.plot([1,2,3], {code_snippet}); "
                f"plt.savefig('{label}.png'); plt.close('all')",
        }
        if supersede:
            body["supersede_newer"] = True
        return client.post(f"/api/entities/{parent_id}/make_revision", json=body)

    # Build: anchor → rev2 → rev3 (rev2 and rev3 stack on each other),
    # then supersede both with a new revision from anchor.
    r2 = _post_revise(anchor, "[2,4,6]", "v2")
    check("seed rev2 from anchor 200", r2.status_code == 200, f"got {r2.status_code}")
    rev2 = r2.json()["entity"]["id"]

    r3 = _post_revise(rev2, "[3,6,9]", "v3")
    check("seed rev3 from rev2 200", r3.status_code == 200, f"got {r3.status_code}")
    rev3 = r3.json()["entity"]["id"]

    r4 = _post_revise(anchor, "[9,9,9]", "v4_super", supersede=True)
    check("rev4 from anchor with supersede 200", r4.status_code == 200, f"got {r4.status_code}")
    rev4 = r4.json()["entity"]["id"]
    check("rev4 superseded set includes both",
          {rev2, rev3}.issubset(set(r4.json()["superseded"])),
          f"got {r4.json()['superseded']}")

    # Default chain (UI mode) — visible only
    visible = [e["id"] for e in figure_history(anchor)]
    check("visible chain length 2 (anchor + rev4)", len(visible) == 2, f"got {visible}")
    check("visible newest is rev4", visible[0] == rev4, f"got {visible}")
    check("visible oldest is anchor", visible[-1] == anchor, f"got {visible}")

    # Admin chain — include superseded
    full_ids = {e["id"] for e in figure_history(anchor, include_superseded=True)}
    check("full chain includes rev2", rev2 in full_ids, f"got {full_ids}")
    check("full chain includes rev3", rev3 in full_ids, f"got {full_ids}")
    check("full chain includes rev4", rev4 in full_ids, f"got {full_ids}")
    check("full chain includes anchor", anchor in full_ids, f"got {full_ids}")
    check("full chain length 4 (anchor + rev2 + rev3 + rev4)",
          len(full_ids) == 4, f"got {full_ids}")


def main() -> int:
    test_full_lifecycle_through_http()
    test_admin_include_superseded_surfaces_all()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL FOCUSED-RESULT REDESIGN E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
