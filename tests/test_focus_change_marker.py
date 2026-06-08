"""Fix B for the focus-handling regression (2026-06-07/08 thr_b80bc612):
when the user navigates to a different entity between sends, a
'[Focus changed: …]' marker is prepended to the new turn's user-message
content in the LLM-call history. This is the structural counterweight
to the conversation-history recency bias that kept the agent's 'active
entity' pointer stuck on the prior focus.

Contracts:
  1. Focus A → focus B (same thread, both real entities): marker fires
     and names both old + new entities + ids; new-focus members listed.
  2. Workspace → entity: marker fires (transition from no-focus to focus).
  3. Entity → same entity (no change): marker does NOT fire.
  4. Workspace → workspace (no change): marker does NOT fire.
  5. First user message in a thread (no prior to compare against):
     marker does NOT fire (nothing to announce).
  6. annotation_image attached: marker does NOT fire (image is dominant
     context; we don't stack markers/trailers on image turns).
  7. Marker is ephemeral — not persisted to the DB (same lifecycle as
     the focus trailer + annotation_note).
  8. Idempotency: re-rendering the same prompt twice (re-running
     stream_response on the SAME stored conversation) yields the SAME
     marker text — comparison is over stored fields, not transient
     navigation events.
"""
from __future__ import annotations
import asyncio
import json as _json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_focus_marker_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "m.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = "/workspace/aba-runtime/envs"
_fake = Path(_tmp) / "fake.jsonl"
# Two turns of fake responses so we can drive the same thread twice
_fake.write_text(
    _json.dumps({"blocks": [{"type": "text", "text": "ok"}]}) + "\n" +
    _json.dumps({"blocks": [{"type": "text", "text": "ok"}]}) + "\n" +
    _json.dumps({"blocks": [{"type": "text", "text": "ok"}]}) + "\n" +
    _json.dumps({"blocks": [{"type": "text", "text": "ok"}]}) + "\n"
)
os.environ["ABA_FAKE_SESSION"] = str(_fake)
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, WORKSPACE_ID    # noqa: E402
import content.bio                                       # noqa: F401, E402
from core.graph.messages import get_messages             # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _flat_text(content) -> list[str]:
    if isinstance(content, list):
        return [b.get("text") or "" for b in content
                if isinstance(b, dict) and b.get("type") == "text"]
    return []


def _seed_figure(tid: str):
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = ("import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
            "plt.figure();plt.plot([1,2,3],[1,4,9]);plt.savefig('s.png');plt.close('all')")
    res = run_python({"code": code}, ctx={"thread_id": tid, "tool_use_id": f"tu_{tid}"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=tid,
    )
    return pin_artifact(res["exec_id"], "figure", 0,
                        wrap_in_result=False, thread_id=tid)["entity_id"]


def _result_with(fig_id: str, title: str, tid: str) -> str:
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    r = client.post("/api/results", json={
        "thread_id": tid, "title": title,
        "members": [{"kind": "figure", "ref": fig_id}],
    })
    return r.json()["id"]


def _capture_open_stream():
    """Monkey-patch make_open_stream so each stream_response call's
    final history is captured for assertions."""
    import core.llm as _llm
    captured: list[list[dict]] = []
    orig = _llm.make_open_stream

    def factory():
        real = orig()
        def open_stream(history, tools, system="", model=None, dynamic_system=""):
            snap = []
            for m in history:
                c = m.get("content")
                snap.append({"role": m.get("role"),
                             "content": [dict(b) if isinstance(b, dict) else b
                                         for b in (c or [])]
                             if isinstance(c, list) else c})
            captured.append(snap)
            return real(history, tools, system=system, model=model,
                        dynamic_system=dynamic_system)
        return open_stream

    _llm.make_open_stream = factory  # type: ignore[assignment]
    import importlib, guide as _guide
    importlib.reload(_guide)
    return captured, orig, _llm


def _drain(*, focus_entity_id: str | None, thread_id: str, user_text: str,
           annotation_image: str | None = None):
    from guide import stream_response
    async def go():
        async for _ in stream_response(
            user_text=user_text,
            focus_entity_id=focus_entity_id or WORKSPACE_ID,
            thread_id=thread_id,
            annotation_image=annotation_image,
        ):
            pass
    asyncio.get_event_loop().run_until_complete(go())


def _last_user_text(captured) -> str:
    history = captured[-1]
    user_msgs = [m for m in history if m.get("role") == "user"]
    if not user_msgs:
        return ""
    return " || ".join(_flat_text(user_msgs[-1].get("content")))


def test_change_between_two_results_fires_marker():
    print("\n[1] focus A (Result) → focus B (Result) → marker fires, names both, lists new members")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fa = _seed_figure("thr_m1")
        fb = _seed_figure("thr_m1")
        ra = _result_with(fa, "Result A", "thr_m1")
        rb = _result_with(fb, "Result B", "thr_m1")
        # Turn 1: user on A. Marker should NOT fire (first user msg).
        _drain(focus_entity_id=ra, thread_id="thr_m1", user_text="hello on A")
        first_text = _last_user_text(captured)
        check("first turn has NO marker (no prior user msg)",
              "[Focus changed" not in first_text,
              f"got: {first_text[:300]!r}")
        # Turn 2: user navigates to B, sends.
        _drain(focus_entity_id=rb, thread_id="thr_m1", user_text="now on B")
        second_text = _last_user_text(captured)
        check("second turn marker fires", "[Focus changed" in second_text,
              f"got: {second_text[:400]!r}")
        check("marker names OLD focus (Result A)",
              "'Result A'" in second_text and ra in second_text)
        check("marker names NEW focus (Result B)",
              "'Result B'" in second_text and rb in second_text)
        check("marker lists new-focus member id (fb)",
              fb in second_text, f"member fb={fb}")
        check("marker does NOT list old-focus member id (fa)",
              fa not in second_text, f"old member fa={fa} unexpectedly in: {second_text[-300:]!r}")
        check("marker mentions 'NEW focus'", "NEW focus" in second_text)
        check("marker is prepended (appears BEFORE user_text)",
              second_text.find("[Focus changed") < second_text.find("now on B"),
              f"got positions: marker={second_text.find('[Focus changed')}, text={second_text.find('now on B')}")
    finally:
        _llm.make_open_stream = orig


def test_workspace_to_entity_fires_marker():
    print("\n[2] workspace → entity → marker fires (going from no-focus to focus)")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fa = _seed_figure("thr_m2")
        ra = _result_with(fa, "Some Result", "thr_m2")
        _drain(focus_entity_id=WORKSPACE_ID, thread_id="thr_m2", user_text="general question")
        # Turn 2: now focused on a Result
        _drain(focus_entity_id=ra, thread_id="thr_m2", user_text="now ask about this")
        text = _last_user_text(captured)
        check("marker fires on workspace→entity", "[Focus changed" in text,
              f"got: {text[:300]!r}")
        check("marker says 'workspace'", "workspace" in text)
        check("marker names the new entity", ra in text)
    finally:
        _llm.make_open_stream = orig


def test_unchanged_focus_no_marker():
    print("\n[3] focus unchanged across turns → marker does NOT fire")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fa = _seed_figure("thr_m3")
        ra = _result_with(fa, "Result Stable", "thr_m3")
        _drain(focus_entity_id=ra, thread_id="thr_m3", user_text="first ask")
        _drain(focus_entity_id=ra, thread_id="thr_m3", user_text="second ask, same focus")
        text = _last_user_text(captured)
        check("no marker when focus unchanged",
              "[Focus changed" not in text, f"got: {text[:300]!r}")
    finally:
        _llm.make_open_stream = orig


def test_first_message_no_marker():
    print("\n[4] first user message in thread → no marker (nothing to compare against)")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fa = _seed_figure("thr_m4")
        ra = _result_with(fa, "Result Solo", "thr_m4")
        _drain(focus_entity_id=ra, thread_id="thr_m4", user_text="my very first message")
        text = _last_user_text(captured)
        check("no marker on first message",
              "[Focus changed" not in text, f"got: {text[:300]!r}")
    finally:
        _llm.make_open_stream = orig


def test_annotation_image_suppresses_marker():
    print("\n[5] annotation_image attached → marker suppressed (image is dominant)")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fa = _seed_figure("thr_m5")
        fb = _seed_figure("thr_m5")
        ra = _result_with(fa, "Result A2", "thr_m5")
        rb = _result_with(fb, "Result B2", "thr_m5")
        _drain(focus_entity_id=ra, thread_id="thr_m5", user_text="first on A")
        b64_1x1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
        _drain(focus_entity_id=rb, thread_id="thr_m5",
               user_text="now on B with image", annotation_image=b64_1x1)
        text = _last_user_text(captured)
        check("no focus-change marker on image turn",
              "[Focus changed" not in text,
              f"got: {text[:400]!r}")
    finally:
        _llm.make_open_stream = orig


def test_marker_not_persisted():
    print("\n[6] marker is ephemeral — does NOT enter persisted DB user messages")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fa = _seed_figure("thr_m6")
        fb = _seed_figure("thr_m6")
        ra = _result_with(fa, "R6a", "thr_m6")
        rb = _result_with(fb, "R6b", "thr_m6")
        _drain(focus_entity_id=ra, thread_id="thr_m6", user_text="first")
        _drain(focus_entity_id=rb, thread_id="thr_m6", user_text="second with change")
        # In-memory should have marker
        text_inmem = _last_user_text(captured)
        check("LLM-call history has the marker (in-memory)",
              "[Focus changed" in text_inmem)
        # DB should NOT
        persisted = get_messages(WORKSPACE_ID, thread_id="thr_m6")
        user_msgs = [m for m in persisted if m.get("role") == "user"]
        for um in user_msgs:
            texts = _flat_text(um.get("content"))
            joined = " || ".join(texts)
            check("persisted user msg has no marker text",
                  "[Focus changed" not in joined,
                  f"leaked into DB user_text: {joined!r}")
    finally:
        _llm.make_open_stream = orig


def test_idempotent_helper():
    """Idempotency: the marker is derived from two stored fields (prev
    + current focus_entity_id). Same inputs → identical output. No
    transient navigation event to remember; nothing depends on call
    count or wall-clock. This means re-running the same prompt twice
    yields the same marker."""
    print("\n[7] _build_focus_change_marker idempotency: same inputs → same output")
    init_db()
    fa = _seed_figure("thr_m7")
    fb = _seed_figure("thr_m7")
    ra = _result_with(fa, "R7a", "thr_m7")
    rb = _result_with(fb, "R7b", "thr_m7")
    from guide import _build_focus_change_marker
    # Genuine change → marker
    m1 = _build_focus_change_marker(ra, rb)
    m2 = _build_focus_change_marker(ra, rb)
    check("repeated calls (change) yield identical markers", m1 == m2,
          f"m1={m1!r}\nm2={m2!r}")
    check("change call returns a string", isinstance(m1, str), f"m1={m1!r}")
    # Same focus both sides → None, both times
    n1 = _build_focus_change_marker(ra, ra)
    n2 = _build_focus_change_marker(ra, ra)
    check("repeated calls (no change) both return None",
          n1 is None and n2 is None,
          f"n1={n1!r} n2={n2!r}")
    # No prior → None
    p1 = _build_focus_change_marker(None, rb)
    check("no-prior call returns None (first turn case)",
          p1 is None, f"p1={p1!r}")


def main() -> int:
    test_change_between_two_results_fires_marker()
    test_workspace_to_entity_fires_marker()
    test_unchanged_focus_no_marker()
    test_first_message_no_marker()
    test_annotation_image_suppresses_marker()
    test_marker_not_persisted()
    test_idempotent_helper()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL FOCUS-CHANGE-MARKER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
