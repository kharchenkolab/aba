"""
Conversation integrity — every `tool_result` sent to the Anthropic API must be
preceded by an assistant message carrying the matching `tool_use`, or the API
400s ("tool_result … without a corresponding tool_use"). That BadRequest looped
"Something went wrong" in a live session and likely fed the model a broken
history → a fabricated success.

`_ensure_tool_pair_completeness` repairs FORWARD orphans (tool_use → no result);
this checks it ALSO repairs BACKWARD/leading orphans (a tool_result with no
preceding matching tool_use — from a rolling-summary cut between a tool_use and
its result, or a cancel/error that dropped the assistant turn).

Deterministic (no model). Run:
    .venv/bin/python tests/d11_conversation_integrity.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ABA_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="aba_d11_"), "t.db"))

from guide import _ensure_tool_pair_completeness as fix  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def api_valid(msgs) -> bool:
    """The API contract: each tool_result is immediately preceded by an
    assistant message carrying its tool_use id."""
    for i, m in enumerate(msgs):
        for b in m.get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                prev = msgs[i - 1] if i > 0 else None
                if not prev or prev["role"] != "assistant":
                    return False
                tu = {x.get("id") for x in prev["content"]
                      if isinstance(x, dict) and x.get("type") == "tool_use"}
                if b.get("tool_use_id") not in tu:
                    return False
    return True


def has_tool_result(msgs) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_result"
               for m in msgs for b in m.get("content", []))


def tr(tid):  return {"type": "tool_result", "tool_use_id": tid, "content": "x"}
def tu(tid):  return {"type": "tool_use", "id": tid, "name": "t", "input": {}}
def txt(s):   return {"type": "text", "text": s}


def main() -> int:
    print("tool-pair repair")
    # 1. leading orphan (history starts with a tool_result)
    r = fix([{"role": "user", "content": [tr("a")]},
             {"role": "assistant", "content": [txt("hi")]}])
    check("leading orphan stripped → API-valid", api_valid(r))

    # 2. post-summary orphan (summary user msg, then orphaned tool_result)
    r = fix([{"role": "user", "content": [txt("[Summary …]")]},
             {"role": "user", "content": [tr("a")]},
             {"role": "assistant", "content": [txt("ok")]}])
    check("post-summary orphan stripped → API-valid", api_valid(r))

    # 3. mismatched id (result id != the preceding tool_use id)
    r = fix([{"role": "assistant", "content": [tu("x")]},
             {"role": "user", "content": [tr("y")]}])
    check("mismatched id reconciled → API-valid", api_valid(r))

    # 4. valid pair must survive untouched
    r = fix([{"role": "assistant", "content": [tu("x")]},
             {"role": "user", "content": [tr("x")]}])
    check("valid pair preserved", api_valid(r) and has_tool_result(r))

    # 5. forward orphan (tool_use, no result) must be filled, and stay valid
    r = fix([{"role": "assistant", "content": [tu("x")]},
             {"role": "assistant", "content": [txt("next")]}])
    check("forward orphan filled + valid", api_valid(r) and has_tool_result(r))

    # 6. a normal text-only conversation is unchanged-valid
    conv = [{"role": "user", "content": [txt("hello")]},
            {"role": "assistant", "content": [txt("hi there")]}]
    check("plain conversation untouched", fix(conv) == conv)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures)); return 1
    print("ALL CONVERSATION-INTEGRITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
