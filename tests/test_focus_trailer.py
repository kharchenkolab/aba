"""Focus-trailer reminder: appended ephemerally after the user's text
when focus is set, naming the focused entity + members + ids. The
trailer combats the recency-bias failure where conversation-history
priors outweigh the top-of-prompt focus card (live regression
2026-06-07 thr_b80bc612 — agent passed entity_id of UMAP-from-prior-
turn to make_revision while focus was the heatmap Result).

Verifies:
  - Trailer is appended to the LAST user message in the LLM-call history
    (not persisted to the DB — same lifecycle as annotation_note)
  - Trailer mentions the focused entity's id
  - For a Result, trailer lists member ids
  - For a member with a revision chain, trailer cites the DISPLAYED
    (latest) revision id, not the anchor
  - No trailer when focus is workspace
  - No trailer when annotation_image is set (image-trailer takes over)
"""
from __future__ import annotations
import asyncio
import json as _json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_focus_trailer_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "t.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = str(Path(_tmp) / "envs")
_fake = Path(_tmp) / "fake.jsonl"
_fake.write_text(_json.dumps({"blocks": [{"type": "text", "text": "ok"}]}) + "\n")
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
            "plt.figure();plt.plot([1,2,3],[1,4,9]);plt.savefig('p.png');plt.close('all')")
    res = run_python({"code": code}, ctx={"thread_id": tid, "tool_use_id": f"tu_{tid}"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=tid,
    )
    return pin_artifact(res["exec_id"], "figure", 0,
                        wrap_in_result=False, thread_id=tid)["entity_id"]


def _revise(eid: str, y: float):
    from content.bio.lifecycle.revisions import make_revision
    code = (f"import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
            f"plt.figure();plt.plot([1,2,3],[{y},{y+1},{y+2}]);plt.savefig('r.png');plt.close('all')")
    return make_revision(eid, code)["new_entity_id"]


def _result_with_figure(fig_id: str, tid: str) -> str:
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    r = client.post("/api/results", json={
        "thread_id": tid, "title": "Test result holding a figure",
        "members": [{"kind": "figure", "ref": fig_id}],
    })
    return r.json()["id"]


def _drain_stream(focus_entity_id: str, *, thread_id: str,
                  annotation_image: str | None = None,
                  annotation_note: str | None = None,
                  user_text: str = "what figure am I looking at?"):
    """Drive one turn through stream_response in fake-session mode."""
    from guide import stream_response
    async def go():
        async for _ in stream_response(
            user_text=user_text,
            focus_entity_id=focus_entity_id,
            thread_id=thread_id,
            annotation_image=annotation_image,
            annotation_note=annotation_note,
        ):
            pass
    asyncio.get_event_loop().run_until_complete(go())


def _last_call_history_path(thread_id: str) -> Path:
    """Find the JSONL transcript of the most recent run for `thread_id`.
    The runner writes to ABA_RUNTIME_DIR/runs/<run_id>/messages.jsonl
    (or similar). Locate by scanning for one mentioning the trailer."""
    # The fake-session path doesn't write run transcripts by default;
    # we instead assert via in-memory hooks. Use the open_stream factory
    # to capture the history that was sent to the LLM.
    raise NotImplementedError("Use _capture_open_stream instead")


def _capture_open_stream():
    """Monkey-patch make_open_stream so each stream_response call's
    final history (the messages it would have sent to the LLM) is
    captured for assertions. Returns a callable to read the latest."""
    import core.llm as _llm
    captured: list[list[dict]] = []
    orig = _llm.make_open_stream

    def factory():
        real = orig()
        def open_stream(history, tools, system="", model=None, dynamic_system=""):
            # Snapshot the messages passed in (deep-ish copy of content lists)
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
    # Also reload guide to pick up the new factory (it caches open_stream at import)
    import importlib, guide as _guide
    importlib.reload(_guide)
    return captured, orig, _llm


def test_trailer_present_for_focused_result():
    print("\n[1] focus=Result → trailer appended after user's text in LLM-call history")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fig = _seed_figure("thr_t1")
        rid = _result_with_figure(fig, "thr_t1")
        _drain_stream(rid, thread_id="thr_t1")
        check("at least one open_stream call captured", len(captured) >= 1,
              f"got {len(captured)}")
        if not captured:
            return
        history = captured[-1]
        # The LAST user message should have the trailer appended
        user_msgs = [m for m in history if m.get("role") == "user"]
        check("user message present", len(user_msgs) >= 1)
        if not user_msgs:
            return
        texts = _flat_text(user_msgs[-1].get("content"))
        joined = " || ".join(texts)
        check("trailer marker '[Reminder: focused on' present",
              "[Reminder: focused on" in joined,
              f"joined={joined[:400]!r}")
        check("trailer names the Result id", rid in joined, f"rid={rid}")
        check("trailer names the figure member id", fig in joined,
              f"member fig={fig}")
        check("trailer mentions 'use these ids'", "use these ids" in joined,
              f"joined={joined[-200:]!r}")
    finally:
        _llm.make_open_stream = orig  # type: ignore[assignment]


def test_trailer_cites_displayed_revision_not_anchor():
    """When a member has a revision chain, the panel displays chain[0]
    (latest). The trailer must cite the LATEST id so make_revision
    gets the right entity, not the anchor."""
    print("\n[2] member with revision chain → trailer cites LATEST id, not anchor")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        anchor = _seed_figure("thr_t2")
        rev2 = _revise(anchor, 5.0)
        rev3 = _revise(rev2, 9.0)
        rid = _result_with_figure(anchor, "thr_t2")
        _drain_stream(rid, thread_id="thr_t2")
        history = captured[-1]
        texts = _flat_text([m for m in history if m["role"] == "user"][-1]["content"])
        joined = " || ".join(texts)
        check("trailer cites rev3 (latest)", rev3 in joined, f"got: {joined[:400]!r}")
        check("trailer does NOT cite anchor as the figure id when chain exists",
              anchor not in joined, f"anchor={anchor} unexpectedly in trailer; chain[0]={rev3}")
    finally:
        _llm.make_open_stream = orig


def test_no_trailer_for_workspace_focus():
    print("\n[3] focus=workspace → no trailer (no specific entity to anchor on)")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        _drain_stream(WORKSPACE_ID, thread_id="thr_t3", user_text="hello")
        history = captured[-1]
        texts = _flat_text([m for m in history if m["role"] == "user"][-1]["content"])
        joined = " || ".join(texts)
        check("no focus trailer in workspace mode",
              "[Reminder: focused on" not in joined,
              f"got: {joined[:200]!r}")
    finally:
        _llm.make_open_stream = orig


def test_no_trailer_when_image_attached():
    """When annotation_image is set, the existing image trailer fires.
    Don't stack a second focus trailer — they'd compete."""
    print("\n[4] annotation_image set → focus trailer skipped (image trailer wins)")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fig = _seed_figure("thr_t4")
        rid = _result_with_figure(fig, "thr_t4")
        # Use a tiny valid base64 PNG (1x1 transparent)
        b64_1x1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
        _drain_stream(rid, thread_id="thr_t4",
                      annotation_image=b64_1x1,
                      annotation_note="The user is asking about something visual.")
        history = captured[-1]
        texts = _flat_text([m for m in history if m["role"] == "user"][-1]["content"])
        joined = " || ".join(texts)
        check("no focus-trailer text when image attached",
              "[Reminder: focused on" not in joined,
              f"got: {joined[:300]!r}")
    finally:
        _llm.make_open_stream = orig


def test_trailer_not_persisted_to_db():
    """Same lifecycle as the image trailer / annotation_note ephemeral
    injection — the trailer must NOT enter the persisted user message."""
    print("\n[5] trailer is ephemeral (in-memory only) — DB user msg has just user_text")
    init_db()
    captured, orig, _llm = _capture_open_stream()
    try:
        fig = _seed_figure("thr_t5")
        rid = _result_with_figure(fig, "thr_t5")
        _drain_stream(rid, thread_id="thr_t5", user_text="please make a PDF")
        # Pull from DB
        persisted = get_messages(WORKSPACE_ID, thread_id="thr_t5")
        user_msgs = [m for m in persisted if m.get("role") == "user"]
        check("persisted user msg exists", len(user_msgs) >= 1)
        if user_msgs:
            texts = _flat_text(user_msgs[0].get("content"))
            joined = " || ".join(texts)
            check("persisted msg contains user_text",
                  "please make a PDF" in joined, f"got: {joined!r}")
            check("persisted msg does NOT contain trailer",
                  "[Reminder: focused on" not in joined,
                  f"trailer leaked into DB: {joined!r}")
    finally:
        _llm.make_open_stream = orig


def main() -> int:
    test_trailer_present_for_focused_result()
    test_trailer_cites_displayed_revision_not_anchor()
    test_no_trailer_for_workspace_focus()
    test_no_trailer_when_image_attached()
    test_trailer_not_persisted_to_db()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL FOCUS-TRAILER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
