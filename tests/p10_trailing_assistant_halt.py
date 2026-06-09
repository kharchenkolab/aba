"""#13 — the Guide must NOT send an LLM request whose last message is an
assistant turn (the Anthropic 'no assistant prefill' 400 the OAuth model
returns). Such a state means the conversation has nothing to answer; the loop
should halt cleanly with a `done` instead of calling the model.

We drive stream_response directly with retry=True (which regenerates without
appending a new user message) over a history that ends with an assistant text
message, and replace guide.open_stream with a TRIPWIRE that fails the test if
the model is ever called. The guard must short-circuit before it.

Token-free (FAKE_SESSION). Isolated temp DB. Deterministic.

Run:
    .venv/bin/python tests/p10_trailing_assistant_halt.py
"""
from __future__ import annotations
import os
import sys
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="aba_p13_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = str(Path(_TMP) / "p13.db")           # SINGLE mode + isolation
os.environ["ABA_FAKE_SESSION"] = str(ROOT / "tests/fixtures/list_files.jsonl")
sys.path.insert(0, str(ROOT / "backend"))

import guide                                                     # noqa: E402
from core.graph._schema import init_db, WORKSPACE_ID            # noqa: E402
from core.graph.messages import append_message                  # noqa: E402
from core.graph.threads import get_or_create_default_thread     # noqa: E402

init_db()


class _Tripwire:
    """Stands in for guide.open_stream. If the guard fails to halt, the loop
    calls this and the test fails loudly (no network, no tokens)."""
    called = False

    def __call__(self, *a, **k):
        _Tripwire.called = True
        raise AssertionError("open_stream was called — #13 guard did NOT halt "
                             "on a history ending with an assistant message")


async def _drive_retry_over_assistant_terminated_history():
    tid = get_or_create_default_thread()
    # Seed a history whose LAST message is an assistant *text* turn (a completed
    # reply — nothing for the model to answer).
    append_message("user", [{"type": "text", "text": "hi"}],
                   entity_id=WORKSPACE_ID, thread_id=tid)
    append_message("assistant", [{"type": "text", "text": "Here is the answer."}],
                   entity_id=WORKSPACE_ID, thread_id=tid)

    guide.open_stream = _Tripwire()      # monkeypatch the module-global

    events = []
    async for ev in guide.stream_response("", thread_id="default", retry=True):
        events.append(ev)
    return events


def test_halts_without_calling_model_and_emits_done():
    events = asyncio.run(_drive_retry_over_assistant_terminated_history())
    types = [e.get("type") for e in events]
    assert not _Tripwire.called, "the model was called despite a trailing-assistant history"
    assert "done" in types, f"no clean 'done' emitted; got {types}"
    # It must NOT have emitted an error/cancelled — this is a graceful halt.
    assert "error" not in types, f"unexpected error event: {events}"
    assert "cancelled" not in types, f"unexpected cancelled event: {events}"
    print(f"  events: {types}")


def main() -> int:
    failed = []
    for t in [test_halts_without_calling_model_and_emits_done]:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    if failed:
        print(f"\n{len(failed)} failed")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
