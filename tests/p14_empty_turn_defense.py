"""#14 — degenerate-empty turn defense (found live 2026-07-19: asked for a
pin, opus emitted a 5-token nothing, the turn ended in wordless silence and
an EMPTY assistant message landed in history).

The guide loop must (1) never persist an empty assistant message, (2) retry
the generation once when a completed generation produced no output at all,
and (3) land an honest marker instead of silence if the retry is empty too.

Token-free (FAKE_SESSION replay). Isolated temp DB. Deterministic.

Run:
    .venv/bin/python tests/p14_empty_turn_defense.py
"""
from __future__ import annotations
import os
import sys
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="aba_p14_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = str(Path(_TMP) / "p14.db")
_FIXTURE = Path(_TMP) / "empty_then_text.jsonl"
_FIXTURE.write_text(
    '{"blocks": []}\n'
    '{"blocks": [{"type": "text", "text": "Recovered: the answer is '
    'forty-two."}]}\n')
os.environ["ABA_FAKE_SESSION"] = str(_FIXTURE)
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.content_pack import set_active_pack            # noqa: E402
from content.bio import BIO_PACK                                 # noqa: E402
set_active_pack(BIO_PACK)
BIO_PACK.register_hooks()

import guide                                                     # noqa: E402
from core.graph._schema import init_db, WORKSPACE_ID             # noqa: E402
from core.graph.messages import get_messages                     # noqa: E402
from core.runtime import llm_runtime_fake as fake                # noqa: E402

init_db()


def _assistant_msgs(tid=None):
    msgs = get_messages(WORKSPACE_ID, thread_id=tid)
    return [m for m in msgs if (m.get("role") or "") == "assistant"]


def _texts(m) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c
                       if isinstance(b, dict) and b.get("type") == "text")
    return c or ""


async def _drive(q):
    async for _ in guide.stream_response(q, thread_id="default"):
        pass


def test_empty_generation_retries_once_and_recovers():
    asyncio.run(_drive("pin that as a result please"))
    msgs = _assistant_msgs()
    non_empty = [m for m in msgs if _texts(m).strip()
                 or any(isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in (m.get("content") or []))]
    assert any("Recovered" in _texts(m) for m in non_empty), \
        f"retry did not produce the recovery text: {msgs}"
    empties = [m for m in msgs if not _texts(m).strip()
               and not any(isinstance(b, dict) and b.get("type") == "tool_use"
                           for b in (m.get("content") or []))]
    assert not empties, f"empty assistant message persisted: {empties}"


def test_double_empty_lands_honest_marker():
    fake._Cursor.reset_for_testing([{"blocks": []}, {"blocks": []}])
    asyncio.run(_drive("and now do the next step"))
    msgs = _assistant_msgs()
    assert any("produced no response" in _texts(m) for m in msgs), \
        f"no honest marker after double-empty: {[_texts(m) for m in msgs]}"


def main() -> int:
    failed = []
    for t in [test_empty_generation_retries_once_and_recovers,
              test_double_empty_lands_honest_marker]:
        try:
            t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(t.__name__)
            print(f"  [FAIL] {t.__name__}: {e}")
    print("ALL PASS" if not failed else f"FAILED: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
