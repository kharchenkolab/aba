"""Per-turn spec resolution chain + per-thread persistence.

Covers C1 (thread persistence) + C2 (ChatRequest.spec + handler
resolution) + C3 (this test module). The resolver itself is the
single point of truth — guide.py just feeds it the three inputs.

Precedence (highest → lowest):
  1. request override (e.g. ChatRequest.spec)
  2. thread.metadata.spec
  3. ABA_PRIMARY_SPEC env / "guide" default
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_thread_spec_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "ts.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))


pytestmark = pytest.mark.bio


# ── 1. resolver precedence chain (no DB needed) ─────────────────────
def test_request_override_wins_over_everything(monkeypatch):
    from core.runtime.agent import resolve_spec_for_turn
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "from_env")
    n = resolve_spec_for_turn(
        request_override="from_request", thread_spec="from_thread")
    assert n == "from_request"


def test_thread_spec_wins_over_env(monkeypatch):
    from core.runtime.agent import resolve_spec_for_turn
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "from_env")
    n = resolve_spec_for_turn(thread_spec="from_thread")
    assert n == "from_thread"


def test_env_wins_over_default(monkeypatch):
    from core.runtime.agent import resolve_spec_for_turn
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "from_env")
    n = resolve_spec_for_turn()
    assert n == "from_env"


def test_default_when_nothing_set(monkeypatch):
    from core.runtime.agent import resolve_spec_for_turn
    monkeypatch.delenv("ABA_PRIMARY_SPEC", raising=False)
    n = resolve_spec_for_turn()
    assert n == "guide"


def test_empty_string_request_falls_through(monkeypatch):
    """A UI that clears its dropdown to "" must not pin a literal
    empty spec; it should fall through to the thread/env layer."""
    from core.runtime.agent import resolve_spec_for_turn
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "from_env")
    n = resolve_spec_for_turn(
        request_override="   ", thread_spec=None)
    assert n == "from_env"


def test_empty_thread_spec_falls_through(monkeypatch):
    """Same for thread_spec — a thread row with metadata.spec=""
    must not stick."""
    from core.runtime.agent import resolve_spec_for_turn
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "from_env")
    n = resolve_spec_for_turn(thread_spec="")
    assert n == "from_env"


def test_strips_whitespace_on_override():
    from core.runtime.agent import resolve_spec_for_turn
    n = resolve_spec_for_turn(request_override="  lean_guide  ")
    assert n == "lean_guide"


# ── 2. thread persistence: get/set/round-trip ───────────────────────
def test_thread_spec_unset_returns_none():
    from core.graph._schema import init_db
    from core.graph.threads import create_thread, get_thread_spec
    init_db()
    tid = create_thread(title="plain", question="?")
    assert get_thread_spec(tid) is None


def test_thread_spec_pin_at_create():
    from core.graph._schema import init_db
    from core.graph.threads import create_thread, get_thread_spec
    init_db()
    tid = create_thread(title="lean", question="?", spec="lean_guide")
    assert get_thread_spec(tid) == "lean_guide"


def test_thread_spec_set_after_create_then_clear():
    from core.graph._schema import init_db
    from core.graph.threads import (create_thread, get_thread_spec,
                                     set_thread_spec)
    init_db()
    tid = create_thread(title="x", question="?")
    assert get_thread_spec(tid) is None
    set_thread_spec(tid, "lean_guide")
    assert get_thread_spec(tid) == "lean_guide"
    # Clearing reverts to None (env/default takes over).
    set_thread_spec(tid, None)
    assert get_thread_spec(tid) is None
    # Empty string also clears (defense for UI-clears-dropdown).
    set_thread_spec(tid, "lean_guide")
    set_thread_spec(tid, "")
    assert get_thread_spec(tid) is None


def test_thread_spec_does_not_clobber_other_metadata():
    """Persisted metadata.spec must not delete the existing
    metadata fields (question, open_questions, lifecycle).
    Regression guard for the "I added a key and lost everything"
    bug shape."""
    from core.graph._schema import init_db
    from core.graph.entities import get_entity
    from core.graph.threads import create_thread, set_thread_spec
    init_db()
    tid = create_thread(title="t", question="my question")
    set_thread_spec(tid, "lean_guide")
    ent = get_entity(tid)
    md  = ent.get("metadata") or {}
    assert md.get("question")     == "my question"
    assert md.get("lifecycle")    == "open"
    assert md.get("open_questions") == []
    assert md.get("spec")         == "lean_guide"


def test_thread_spec_two_threads_independent():
    from core.graph._schema import init_db
    from core.graph.threads import (create_thread, get_thread_spec,
                                     set_thread_spec)
    init_db()
    a = create_thread(title="a", spec="lean_guide")
    b = create_thread(title="b")
    assert get_thread_spec(a) == "lean_guide"
    assert get_thread_spec(b) is None
    set_thread_spec(b, "guide")
    assert get_thread_spec(a) == "lean_guide"   # unchanged
    assert get_thread_spec(b) == "guide"


# ── 3. ChatRequest carries the spec field ───────────────────────────
def test_chat_request_spec_field_optional():
    from main import ChatRequest
    # Default None.
    r = ChatRequest(text="hi")
    assert r.spec is None
    # Explicit.
    r2 = ChatRequest(text="hi", spec="lean_guide")
    assert r2.spec == "lean_guide"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
