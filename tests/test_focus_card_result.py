"""Phase 2 of the focus-handling regression fix (2026-06-07,
thr_806a2ced): a Result entity's focus card must name its members so
the agent can answer 'what figure am I looking at?' without falling
back to conversation-history recency. Generic-card (title + status +
tags) is too thin.

Coverage:
  - Result with one figure member: card mentions figure title + id
  - Result with multiple members: all listed in order
  - Result with a figure member that has a revision chain: card names
    the DISPLAYED (latest) revision, not just the anchor (the panel
    shows chain[0] by default)
  - Result with a text note member: prose preview shown inline
  - Empty Result: card flags it as empty (no member list)

Run: .venv/bin/python tests/test_focus_card_result.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_card_result_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "r.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
import content.bio                       # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _seed_figure(thread_id: str = "thr_card"):
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = ("import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
            "plt.figure();plt.plot([1,2,3],[1,4,9]);plt.savefig('f.png');plt.close('all')")
    res = run_python({"code": code}, ctx={"thread_id": thread_id, "tool_use_id": f"tu_{thread_id}"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=thread_id,
    )
    return pin_artifact(res["exec_id"], "figure", 0,
                        wrap_in_result=False, thread_id=thread_id)["entity_id"]


def _revise(eid: str, y: float):
    from content.bio.lifecycle.revisions import make_revision
    code = (f"import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
            f"plt.figure();plt.plot([1,2,3],[{y},{y+1},{y+2}]);plt.savefig('r.png');plt.close('all')")
    return make_revision(eid, code)["new_entity_id"]


def _build_card_with_focus(result_id: str, focus_member_id: str | None) -> str:
    from core.graph.entities import get_entity
    from content.bio.cards.result import build_result_card
    return build_result_card(get_entity(result_id),
                              focus_member_id=focus_member_id)[0]


def _build_card(result_id: str) -> str:
    from core.graph.entities import get_entity
    from content.bio.cards.result import build_result_card
    return build_result_card(get_entity(result_id))[0]


def _make_result(title: str, members_seq: list[dict], thread_id: str = "thr_card") -> str:
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    r = client.post("/api/results", json={
        "thread_id": thread_id, "title": title, "members": members_seq,
    })
    return r.json()["id"]


def test_one_figure_member_listed():
    print("\n[1] Result with one figure → card lists it (title + id)")
    init_db()
    fig = _seed_figure("thr_card_1")
    rid = _make_result("Heatmap result",
                       [{"kind": "figure", "ref": fig}], thread_id="thr_card_1")
    card = _build_card(rid)
    check("card mentions 'Members (1)'", "Members (1)" in card, f"card was:\n{card}")
    check("card lists 'figure' kind", "figure" in card)
    check("card includes the figure id", fig in card,
          f"expected {fig} in: {card[:400]!r}")


def test_multiple_members_listed_in_order():
    print("\n[2] Result with 2 figures → both listed in order")
    init_db()
    f1 = _seed_figure("thr_card_2")
    f2 = _seed_figure("thr_card_2")
    rid = _make_result("Two-fig result",
                       [{"kind": "figure", "ref": f1},
                        {"kind": "figure", "ref": f2}],
                       thread_id="thr_card_2")
    card = _build_card(rid)
    check("card mentions 'Members (2)'", "Members (2)" in card)
    check("card includes f1", f1 in card)
    check("card includes f2", f2 in card)
    # Ordering: f1 should appear before f2 in the rendered text
    check("f1 appears before f2 in card", card.find(f1) < card.find(f2),
          f"positions: f1={card.find(f1)} f2={card.find(f2)}")


def test_revision_chain_displayed_id_surfaced():
    print("\n[3] figure member with revisions → card names DISPLAYED (latest) revision")
    init_db()
    anchor = _seed_figure("thr_card_3")
    rev2 = _revise(anchor, 5.0)
    rev3 = _revise(rev2, 9.0)
    rid = _make_result("Result with chain",
                       [{"kind": "figure", "ref": anchor}],
                       thread_id="thr_card_3")
    card = _build_card(rid)
    # The panel displays chain[0] = rev3; the card should mention rev3
    # AND note that the anchor differs (so the agent knows the user
    # sees the latest revision, not the originally-pinned one).
    check("card mentions the latest revision id", rev3 in card,
          f"card was:\n{card}")
    check("card mentions the anchor id (as anchor)", anchor in card,
          f"card was:\n{card}")
    check("card calls out 'displayed revision'", "displayed revision" in card,
          f"card was:\n{card}")


def test_text_note_member_inline_preview():
    print("\n[4] text-note member → preview shown inline (no entity lookup)")
    init_db()
    fig = _seed_figure("thr_card_4")
    rid = _make_result("Result with note",
                       [{"kind": "figure", "ref": fig},
                        {"kind": "text", "text": "These clusters show clear separation by marker expression."}],
                       thread_id="thr_card_4")
    card = _build_card(rid)
    check("card mentions 'note:'", "note:" in card, f"card was:\n{card}")
    check("card includes the note text", "clear separation" in card,
          f"card was:\n{card}")


def _member_ids(result_id: str) -> list[str]:
    from core.graph.entities import get_entity
    members = (get_entity(result_id).get("metadata") or {}).get("members") or []
    return [m.get("id") for m in members]


def test_focus_member_marks_active_panel_in_multi_member_result():
    """Multi-member Result + focus_member_id from chat → the matching
    member line gets a '← user is looking at this one' marker so the
    agent anchors on it for 'this plot' gestures."""
    print("\n[6] multi-member Result + focus_member_id → ← marker on the right line")
    init_db()
    f1 = _seed_figure("thr_card_6")
    f2 = _seed_figure("thr_card_6")
    rid = _make_result("Two-fig result",
                       [{"kind": "figure", "ref": f1},
                        {"kind": "figure", "ref": f2}],
                       thread_id="thr_card_6")
    mids = _member_ids(rid)
    card = _build_card_with_focus(rid, focus_member_id=mids[1])
    check("← marker present in card", "← user is looking at this one" in card,
          f"card was:\n{card}")
    # The marker must be on m_two's line, not m_one's
    lines = card.splitlines()
    f1_line = next(l for l in lines if f1 in l)
    f2_line = next(l for l in lines if f2 in l)
    check("← marker is on f2's line", "←" in f2_line, f"f2_line={f2_line!r}")
    check("← marker is NOT on f1's line", "←" not in f1_line, f"f1_line={f1_line!r}")
    check("header mentions in-view callout",
          "the user has one of these in their viewport" in card,
          f"card was:\n{card}")


def test_focus_member_suppressed_for_single_member_result():
    """Single-member Result: focus_member_id is irrelevant — no marker,
    no header callout. Single-panel behavior unchanged."""
    print("\n[7] single-member Result + focus_member_id → no marker (suppressed)")
    init_db()
    fig = _seed_figure("thr_card_7")
    rid = _make_result("Solo result",
                       [{"kind": "figure", "ref": fig}],
                       thread_id="thr_card_7")
    mids = _member_ids(rid)
    card = _build_card_with_focus(rid, focus_member_id=mids[0])
    check("no ← marker for single-member result", "←" not in card,
          f"card was:\n{card}")
    check("no in-view header callout",
          "the user has one of these" not in card, f"card was:\n{card}")


def test_focus_member_stale_id_is_ignored():
    """A focus_member_id that doesn't match any member (stale from a
    prior focus) is silently dropped — no marker, no error. Defends
    against retry-with-stale-payload paths."""
    print("\n[8] stale focus_member_id → silently ignored (no marker)")
    init_db()
    f1 = _seed_figure("thr_card_8")
    f2 = _seed_figure("thr_card_8")
    rid = _make_result("Two-fig result",
                       [{"kind": "figure", "ref": f1},
                        {"kind": "figure", "ref": f2}],
                       thread_id="thr_card_8")
    card = _build_card_with_focus(rid, focus_member_id="m_does_not_exist")
    check("stale id → no ← marker", "←" not in card, f"card was:\n{card}")
    check("members still listed normally", "Members (2)" in card,
          f"card was:\n{card}")


def test_legacy_builder_signature_still_works():
    """Sanity: legacy callers that invoke build_result_card with just
    the entity (no kwarg) still get the unmarked card. The kwarg has
    a default of None; the gate suppresses the marker in that case."""
    print("\n[9] legacy 1-arg call → no marker, no error")
    init_db()
    f1 = _seed_figure("thr_card_9")
    rid = _make_result("Result",
                       [{"kind": "figure", "ref": f1}],
                       thread_id="thr_card_9")
    # Call without the kwarg, mirroring the pre-2026-06-13 call shape.
    from core.graph.entities import get_entity
    from content.bio.cards.result import build_result_card
    text, _fields = build_result_card(get_entity(rid))
    check("legacy call returns text without marker", "←" not in text,
          f"text was:\n{text}")


def test_empty_result_flagged():
    print("\n[5] empty Result → card flags it as empty (no member list)")
    init_db()
    rid = _make_result("Empty result", [], thread_id="thr_card_5")
    card = _build_card(rid)
    check("card mentions 'no members yet'",
          "no members yet" in card, f"card was:\n{card}")
    check("card does NOT include a Members:N header",
          "Members (" not in card, f"card was:\n{card}")


def main() -> int:
    test_one_figure_member_listed()
    test_multiple_members_listed_in_order()
    test_revision_chain_displayed_id_surfaced()
    test_text_note_member_inline_preview()
    test_focus_member_marks_active_panel_in_multi_member_result()
    test_focus_member_suppressed_for_single_member_result()
    test_focus_member_stale_id_is_ignored()
    test_legacy_builder_signature_still_works()
    test_empty_result_flagged()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL FOCUS-CARD-RESULT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
