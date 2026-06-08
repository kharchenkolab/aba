"""Regression guard for the 2026-06-07 focus-handling bug (thr_806a2ced).

The frontend's 'Chat about this figure' SplitButton attaches a framing
note + image to the next chat send. The image has always been
ephemeral (injected into the LLM call's in-memory history; never
written to the DB). The NOTE used to be persisted as a user text
block -- which meant every subsequent turn read 'user said: asking
about umap (entity_id=fig_X)' even after the user navigated to a
different Result.

This test pins down the new contract: when `/api/chat` is called with
an `annotation_note`, the persisted message history contains ONLY the
user's actual text -- the note doesn't enter the DB. (The note is
still delivered to the model for the duration of the turn; we don't
test that here -- the model interaction is exercised by the FAKE_SESSION
prompt-regression suite. This test guards the persistence boundary.)

Run: .venv/bin/python tests/test_annotation_note_ephemeral.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_annot_eph_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "a.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = "/workspace/aba-runtime/envs"
# Activate FAKE_SESSION mode so stream_response doesn't call the LLM.
# Write a one-turn fixture (single trivial text response) and point
# ABA_FAKE_SESSION at it. The persistence branch we're asserting on
# runs BEFORE the fake/real LLM split, so this fixture's content
# doesn't affect what we check.
import json as _json
_fake_fixture = Path(_tmp) / "fake_session.jsonl"
_fake_fixture.write_text(_json.dumps(
    {"blocks": [{"type": "text", "text": "ok"}]}
) + "\n")
os.environ["ABA_FAKE_SESSION"] = str(_fake_fixture)
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db   # noqa: E402
import content.bio                        # noqa: F401, E402
from core.graph.messages import get_messages  # noqa: E402
from core.graph._schema import WORKSPACE_ID  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _flat_text(content) -> list[str]:
    """Pull all 'text' blocks from a message's content (which is a
    list of typed blocks in Claude's content-blocks format)."""
    if isinstance(content, list):
        return [b.get("text") or "" for b in content
                if isinstance(b, dict) and b.get("type") == "text"]
    if isinstance(content, str):
        return [content]
    return []


def _drain(gen):
    """Pull every event from the async generator to completion."""
    async def go():
        async for _ in gen:
            pass
    asyncio.get_event_loop().run_until_complete(go())


def test_persistence_only_user_text():
    print("\n[1] /api/chat with annotation_note → persisted msgs have only user_text")
    init_db()
    from guide import stream_response
    note = ('The user is asking about the run output "umap_leiden.png" '
            '(entity_id="fig_TEST_NOTE_SHOULD_NOT_PERSIST"). '
            'The attached image is that plot — examine it.')
    user_text = "please remove the grid"
    thread_id = "thr_ephemeral_note"
    # Drive one turn. The fake-session path skips the actual LLM call
    # but still appends user messages to the DB before returning.
    _drain(stream_response(
        user_text=user_text,
        thread_id=thread_id,
        annotation_note=note,
        annotation_image=None,    # not relevant for persistence check
    ))
    history = get_messages(WORKSPACE_ID, thread_id=thread_id)
    user_msgs = [m for m in history if m.get("role") == "user"]
    check("at least one user message persisted", len(user_msgs) >= 1,
          f"history len={len(history)}, roles={[m.get('role') for m in history]}")
    # Take the FIRST user message (the turn we just drove)
    if not user_msgs:
        return
    first_user = user_msgs[0]
    texts = _flat_text(first_user.get("content"))
    joined = " | ".join(texts)
    check("persisted user message contains user_text",
          user_text in joined, f"got texts={texts!r}")
    check("note is NOT persisted in the user message",
          "fig_TEST_NOTE_SHOULD_NOT_PERSIST" not in joined,
          f"the note text leaked into persisted history: {joined!r}")
    check("note's framing phrase NOT in persisted message",
          "asking about the run output" not in joined,
          f"got: {joined!r}")
    check("persisted user message has exactly one text block",
          len(texts) == 1, f"got {len(texts)} text blocks: {texts!r}")


def test_persistence_with_no_note():
    print("\n[2] /api/chat without annotation_note still persists user_text normally")
    init_db()
    from guide import stream_response
    user_text = "What's the latest UMAP showing?"
    thread_id = "thr_no_note"
    _drain(stream_response(
        user_text=user_text,
        thread_id=thread_id,
        annotation_note=None,
        annotation_image=None,
    ))
    history = get_messages(WORKSPACE_ID, thread_id=thread_id)
    user_msgs = [m for m in history if m.get("role") == "user"]
    check("user message persisted", len(user_msgs) >= 1)
    if user_msgs:
        texts = _flat_text(user_msgs[0].get("content"))
        check("user_text persisted as the only text block",
              texts == [user_text], f"got {texts!r}")


def main() -> int:
    test_persistence_only_user_text()
    test_persistence_with_no_note()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL ANNOTATION-NOTE-EPHEMERAL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
