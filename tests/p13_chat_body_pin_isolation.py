"""#18 — /api/chat carries project_id in the BODY (invisible to the ASGI pin
middleware), so the handler must bind it explicitly. The turn's project must be
captured from the bound context, NOT the process-global — otherwise a
concurrent request mutating the global mid-handler makes the turn run against
the wrong project's DB.

Teeth: we monkeypatch main.get_entity (called between the pin and start_turn)
to FLIP the process-global to a different project — exactly what a concurrent
request's pin would do if an await sat at that point. The turn must still run
against the project named in the chat body.

  - With the fix (chat handler wraps setup in `with projects.bind(pid)`): the
    flip is inert; the user's turn lands in project A's message log.
  - Without it: start_turn captures the flipped global → the turn lands in the
    OTHER project's log and A's stays empty.

Token-free (FAKE_SESSION). Isolated temp runtime. NOT single-project mode.

Run:
    .venv/bin/python tests/p13_chat_body_pin_isolation.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.pop("ABA_DB_PATH", None)
os.environ.pop("ABA_DB_PATH", None)
_TMP = tempfile.mkdtemp(prefix="aba_p18_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
os.environ["ABA_FAKE_SESSION"] = str(ROOT / "tests/fixtures/list_files.jsonl")
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient                # noqa: E402
from core import projects                                # noqa: E402
from core.graph.messages import get_messages             # noqa: E402
import main                                              # noqa: E402

projects.init()
A = projects.create_project("ChatProjA")["id"]
OTHER = projects.create_project("ChatProjOTHER")["id"]

MARKER = "ping-A-7f3c91"


def _all_text(pid):
    projects.set_current(pid)
    out = []
    for m in get_messages("workspace"):       # all threads in this project's DB
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b["text"])
    return out


def test_chat_turn_binds_body_project_despite_global_flip():
    real_get_entity = main.get_entity

    def _evil_get_entity(eid):
        # Simulate a concurrent request pinning a different project at exactly
        # the point the chat handler validates focus (between pin and capture).
        projects.set_current(OTHER)
        return {"id": eid, "type": "workspace", "title": "ws"}

    main.get_entity = _evil_get_entity
    try:
        with TestClient(main.app) as client:
            with client.stream("POST", "/api/chat",
                               json={"text": MARKER, "project_id": A,
                                     "thread_id": "default"}) as resp:
                assert resp.status_code == 200, resp.status_code
                _ = "".join(resp.iter_text())     # drain until the turn completes
    finally:
        main.get_entity = real_get_entity

    a_texts = _all_text(A)
    other_texts = _all_text(OTHER)
    assert any(MARKER in t for t in a_texts), \
        f"user turn did NOT land in the body project A; A texts={a_texts}"
    assert not any(MARKER in t for t in other_texts), \
        f"user turn LEAKED into the flipped-global project OTHER: {other_texts}"
    print(f"  A got the turn ({len(a_texts)} msgs); OTHER clean ({len(other_texts)} msgs)")


def main_runner() -> int:
    failed = []
    for t in [test_chat_turn_binds_body_project_despite_global_flip]:
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
    sys.exit(main_runner())
