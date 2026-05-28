"""
Deterministic unit repro of the duplicate-tool_result 400: the request-build
shim `_ensure_tool_pair_completeness` must collapse multiple tool_result blocks
that share a tool_use_id (e.g. a reaper 'interrupted' synth fill coexisting with
the real result on a long/shared thread) into one — preferring the real result —
so the messages sent to the API are always valid. No app/model needed.

Run:
    .venv/bin/python tests/p_resume_dedup.py
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["ABA_DB_PATH"] = "/tmp/aba_dedup_unit.db"
sys.path.insert(0, str(ROOT / "backend"))

import guide  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def result_ids(messages):
    ids = []
    for m in messages:
        for b in (m.get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                ids.append(b.get("tool_use_id"))
    return ids


def main() -> int:
    SYNTH = json.dumps({"status": "interrupted", "note": "run was interrupted"})

    # Case 1: synth fill (from the reaper) BEFORE the real result for id X.
    msgs1 = [
        {"role": "user", "content": [{"type": "text", "text": "do it"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "X", "name": "run_python", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "X", "content": SYNTH}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "X", "content": "REAL OUTPUT"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]
    out1 = guide._ensure_tool_pair_completeness(msgs1)
    ids1 = result_ids(out1)
    check("collapses duplicate tool_result for one id", ids1.count("X") == 1, f"ids={ids1}")
    kept = next((b for m in out1 for b in (m.get("content") or [])
                 if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == "X"), None)
    check("keeps the REAL result, not the interrupted synth",
          kept and kept.get("content") == "REAL OUTPUT", str(kept))

    # Case 2: two real results for one id (resume-race style) → keep one.
    msgs2 = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "Y", "name": "t", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "Y", "content": "a"},
                                     {"type": "tool_result", "tool_use_id": "Y", "content": "b"}]},
    ]
    out2 = guide._ensure_tool_pair_completeness(msgs2)
    check("collapses dup within a single message", result_ids(out2).count("Y") == 1, result_ids(out2))

    # Case 3: distinct ids are untouched; genuine missing result still filled.
    msgs3 = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "t", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "A", "content": "ra"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "B", "name": "t", "input": {}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "oops middle orphan"}]},
    ]
    out3 = guide._ensure_tool_pair_completeness(msgs3)
    ids3 = result_ids(out3)
    check("distinct ids preserved + middle orphan filled", ids3.count("A") == 1 and ids3.count("B") == 1, f"ids={ids3}")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL DEDUP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
