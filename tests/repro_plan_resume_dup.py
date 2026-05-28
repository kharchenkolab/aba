"""
Deterministic repro of the plan-Go resume bug: after present_plan halts and the
user approves (resume), the next tool's tool_result gets written into the message
log TWICE for one tool_use id — which the real API rejects (400: "each tool_use
must have a single result"). Fake model, so no API call: we inspect the message
log directly for duplicate tool_result ids.

Run:
    .venv/bin/python tests/repro_plan_resume_dup.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_repro_")
os.environ["ABA_FAKE_SESSION"] = str(ROOT / "tests/fixtures/plan_then_run.jsonl")
# ABA_DB_PATH (not _OVERRIDE) is what _schema actually reads — real isolation.
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "repro.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient          # noqa: E402
from main import app                                # noqa: E402
from core.graph.messages import get_messages        # noqa: E402
from core.graph._schema import WORKSPACE_ID         # noqa: E402

# Instrument append_message: log the caller whenever a tool_result is written,
# so we can see the double-write site.
import traceback                                    # noqa: E402
import core.graph.messages as _msgs                 # noqa: E402
import guide as _guide                              # noqa: E402
_orig_append = _msgs.append_message


def _traced_append(role, content, **kw):
    try:
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    caller = traceback.extract_stack(limit=3)[0]
                    print(f"  [append tool_result {b.get('tool_use_id')}] "
                          f"from {caller.name}:{caller.lineno}")
    except Exception:
        pass
    return _orig_append(role, content, **kw)


_msgs.append_message = _traced_append
_guide.append_message = _traced_append

# Instrument open_stream: dump the tool_result ids in the llm_history sent to
# the model on each call — this is what the real API validates (the 400 is
# about the REQUEST, which the DB may not mirror).
_orig_open = _guide.open_stream
_call = {"n": 0}


def _traced_open(history, tools, system="", model=None):
    _call["n"] += 1
    ids = []
    for m in history:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    ids.append(b.get("tool_use_id"))
    dup = {i: ids.count(i) for i in set(ids) if ids.count(i) > 1}
    flag = f"  <<< DUPLICATE in request: {dup}" if dup else ""
    print(f"  [open_stream #{_call['n']}] tool_result ids in request: {ids}{flag}")
    return _orig_open(history, tools, system=system, model=model)


_guide.open_stream = _traced_open


def parse_sse(body: str):
    return [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]


def tool_result_ids(messages) -> list[str]:
    ids = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                continue
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                ids.append(b.get("tool_use_id"))
    return ids


def main() -> int:
    with TestClient(app) as client:
        # Turn 1: present_plan → halt.
        with client.stream("POST", "/api/chat", json={"text": "do a two-step analysis"}) as r:
            ev = parse_sse("".join(r.iter_text()))
        run_id = next((e.get("run_id") for e in ev if e.get("run_id")), None)
        saw_plan = any(e.get("type") == "plan" for e in ev)
        print(f"turn1: run_id={run_id} saw_plan={saw_plan}")
        assert run_id and saw_plan, "expected a plan halt"

        st = client.get(f"/api/turns/{run_id}").json()
        print(f"turn1 state={st.get('state')}")
        assert st.get("state") == "awaiting_user"

        # Resume: approve the plan → runs run_python, then finishes.
        with client.stream("POST", f"/api/turns/{run_id}/resume",
                           json={"user_text": "go ahead"}) as r:
            ev2 = parse_sse("".join(r.iter_text()))
        kinds = Counter(e.get("type") for e in ev2)
        print(f"resume events: {dict(kinds)}")

    msgs = get_messages(WORKSPACE_ID, thread_id=None)
    ids = tool_result_ids(msgs)
    counts = Counter(i for i in ids if i)
    dups = {k: v for k, v in counts.items() if v > 1}
    print(f"\ntool_result ids in log: {dict(counts)}")
    print("\n--- message roles/blocks ---")
    for i, m in enumerate(msgs):
        content = m["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                content = [{"type": "text"}]
        kinds = [b.get("type") if isinstance(b, dict) else "?" for b in content] if isinstance(content, list) else ["text"]
        print(f"  m{i} {m['role']}: {kinds}")

    if dups:
        print(f"\nBUG REPRODUCED: duplicate tool_result ids → {dups}")
        return 1
    print("\nNO DUPLICATE tool_result — fixed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
