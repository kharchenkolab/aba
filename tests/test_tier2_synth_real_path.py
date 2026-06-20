"""Tier-2 history summarization — REAL-PATH tests.

Bug forensic from prj_30d7535f 2026-06-19:

  The session's pruned history was 147k chars; lean's threshold is 25k;
  Tier-2 silently never fired. Three actual bugs:

    (1) `get_prompt("thread_summary")` returned "" because nothing had
        registered it (test pattern stubbed _synthesize so this was
        invisible).
    (2) `_synthesize` used the global `core.config.MODEL` which the
        user's ABA_MODEL=claude-opus-4-7 made = Opus.
    (3) The catch-all `except Exception: return ""` swallowed the 429
        from Opus's rate budget being shared with the chat loop.

  The pre-existing unit test (test_maybe_summarize_uses_override)
  monkeypatched `_synthesize` to a stub returning "STUB SUMMARY",
  which exercised the THRESHOLD GATE only — not the synth interior.
  All three bugs were invisible behind one stub.

  These tests exercise the real interior. They never patch
  `_synthesize` itself; they only swap the network client or the
  prompt registry, the way bug-driven testing demands.
"""
from __future__ import annotations
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_tier2_real_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "t2.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))


pytestmark = pytest.mark.bio


# Big-enough message list to cross any lean budget and the TAIL_KEEP=20
# guard. Each item ~500 chars × 50 msgs = ~25k chars.
def _big_msgs(n: int = 50) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": [{"type": "text", "text": f"msg #{i} " + ("x" * 480)}]}
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _clean_diag():
    """Reset the Tier-2 diag counters before each test so assertions
    on `calls/ok/skipped_no_prompt/raised` are about THIS test only."""
    from core.summarize.budget_summary import _TIER2_DIAG
    for k in _TIER2_DIAG:
        _TIER2_DIAG[k] = 0 if isinstance(_TIER2_DIAG[k], int) else ""
    yield


# ── 1. Prompt registration is REAL, not assumed ─────────────────────
def test_bio_import_registers_thread_summary_prompt():
    """The bug: a test imports `budget_summary` directly without
    `content.bio`, so the registry is empty, so synth bails. This
    locks in the import-side-effect contract."""
    # Fresh interpreter would prove it cleanly; in-process, we assert
    # the END state after bio has been imported by conftest. If this
    # test fails, either the registration moved or the file vanished.
    import content.bio  # noqa: F401
    from core.prompts import get as get_prompt
    body = get_prompt("thread_summary") or ""
    assert body, "thread_summary prompt is not registered after content.bio import"
    # Sanity-check the body looks like a real summarizer prompt, not
    # a stub. The shipped one mentions structure / neutrality.
    assert len(body) > 500, f"thread_summary prompt is suspiciously short ({len(body)} chars)"


# ── 2. Silent-bail mode is now OBSERVABLE ──────────────────────────
def test_synthesize_records_skipped_when_prompt_missing(monkeypatch):
    """The LIVE bug: no prompt registered → return "" silently. This
    test pretends the prompt is missing and asserts we now log it."""
    import core.summarize.budget_summary as bs
    monkeypatch.setattr("core.prompts.get", lambda name: "")
    out = bs._synthesize("thr_test", _big_msgs(40), prior_summary=None)
    assert out == ""
    diag = bs.tier2_diag()
    assert diag["calls"]               == 1
    assert diag["skipped_no_prompt"]   == 1
    assert diag["ok"]                  == 0
    assert "not registered" in diag["last_error"]


# ── 3. API errors are now OBSERVABLE ──────────────────────────────
class _RaisingClient:
    """Stub sync client that raises a fake 429 — emulates the live
    failure mode without making a real API call."""
    class _Messages:
        def create(self, **kw):
            class _Err(Exception):
                pass
            raise _Err("simulated 429")
    messages = _Messages()


def test_synthesize_records_raised_on_api_error(monkeypatch):
    """The LIVE bug part 2: rate-limit error swallowed by
    `except Exception: return ""`. This test pretends the client
    raises and asserts we now log it (and identify which model)."""
    import core.summarize.budget_summary as bs
    import content.bio  # noqa: F401 — prompt must be registered for this code path
    monkeypatch.setattr("core.llm.sync_anthropic_client",
                        lambda: _RaisingClient())
    out = bs._synthesize("thr_test", _big_msgs(40), prior_summary=None)
    assert out == ""
    diag = bs.tier2_diag()
    assert diag["calls"]    == 1
    assert diag["raised"]   == 1
    assert "simulated 429"  in diag["last_error"]


# ── 4. The model used is the SUMMARY model, not the chat model ─────
def test_synthesize_uses_summary_model_not_global_MODEL(monkeypatch):
    """The LIVE bug part 3: synth used `core.config.MODEL` which the
    user's ABA_MODEL had set to Opus. This test asserts the call
    actually goes to the summary model, regardless of ABA_MODEL."""
    monkeypatch.setenv("ABA_MODEL", "claude-opus-4-7")  # primary chat
    monkeypatch.delenv("ABA_SUMMARY_MODEL", raising=False)
    captured = {}

    class _CapturingClient:
        class _Messages:
            def create(self, **kw):
                captured.update(kw)
                # Return a minimal response shape so synth can finish.
                class _Block:
                    type = "text"
                    text = "wrapped <summary>\nScope: x\nCovers: 40 messages\n...</summary>"
                class _R:
                    content = [_Block()]
                return _R()
        messages = _Messages()

    monkeypatch.setattr("core.llm.sync_anthropic_client",
                        lambda: _CapturingClient())
    import content.bio  # noqa: F401
    import core.summarize.budget_summary as bs
    bs._synthesize("thr_x", _big_msgs(40), prior_summary=None)
    model_used = captured.get("model")
    assert model_used and "haiku" in model_used.lower(), (
        f"Tier-2 used model={model_used!r}, expected a Haiku family "
        "(ABA_MODEL=opus must NOT leak into the summary path)")


def test_summary_model_overridable_via_env(monkeypatch):
    """Forward-looking: once local-LLM lands, ABA_SUMMARY_MODEL
    should redirect Tier-2 there without any code edit."""
    monkeypatch.setenv("ABA_SUMMARY_MODEL", "qwen3-8b-local")
    from core.summarize.budget_summary import _summary_model
    assert _summary_model() == "qwen3-8b-local"


def test_summary_model_defaults_to_haiku(monkeypatch):
    monkeypatch.delenv("ABA_SUMMARY_MODEL", raising=False)
    from core.summarize.budget_summary import _summary_model
    assert "haiku" in _summary_model().lower()


# ── 5. End-to-end: maybe_summarize writes to thread_summaries ────
def test_maybe_summarize_writes_to_thread_summaries_table(monkeypatch):
    """The LIVE bug observable consequence: thread_summaries stays
    empty even after 83 msgs. This test drives the FULL maybe_summarize
    path (NOT patched at the _synthesize boundary) and asserts a row
    lands in the DB."""
    from core.graph._schema import init_db
    init_db()
    captured = {}

    class _GoodClient:
        class _Messages:
            def create(self, **kw):
                captured.update(kw)
                class _Block:
                    type = "text"
                    text = ("<summary>\nScope: thr_e2e\nCovers: many\n"
                            "User asks: things\nAgent did: stuff\n</summary>")
                class _R:
                    content = [_Block()]
                return _R()
        messages = _Messages()

    monkeypatch.setattr("core.llm.sync_anthropic_client",
                        lambda: _GoodClient())
    import content.bio  # noqa: F401
    from core.summarize.budget_summary import maybe_summarize, tier2_diag

    # Force the threshold low so the budget gate fires.
    msgs = _big_msgs(50)   # ~25k chars
    out  = maybe_summarize("thr_e2e", msgs, budget_chars=2000)
    assert len(out) < len(msgs), "summarize should have collapsed the head"
    # First message should be the summary, as a single user-role frame.
    assert out[0]["role"] == "user"
    payload = out[0]["content"]
    if isinstance(payload, list):
        payload = " ".join(b.get("text", "") for b in payload)
    assert "<summary>" in payload
    # And the table now has a row.
    from core.graph._schema import _conn
    with _conn() as c:
        rows = c.execute(
            "SELECT thread_id, covered_until, length(summary) "
            "FROM thread_summaries WHERE thread_id='thr_e2e'"
        ).fetchall()
    assert rows, "thread_summaries empty after maybe_summarize succeeded"
    assert rows[0][1] > 0
    assert rows[0][2] > 50
    # And the diag counter agrees.
    diag = tier2_diag()
    assert diag["calls"] == 1
    assert diag["ok"]    == 1
    assert diag["raised"] == 0


def test_maybe_summarize_below_threshold_does_not_call_synth(monkeypatch):
    """Symmetric regression: when we're UNDER budget, synth must NOT
    be called — saves a Haiku roundtrip per turn under normal load."""
    call_count = {"n": 0}

    class _Tripwire:
        class _Messages:
            def create(self, **kw):
                call_count["n"] += 1
                raise RuntimeError("synth should not have been called")
        messages = _Messages()

    monkeypatch.setattr("core.llm.sync_anthropic_client",
                        lambda: _Tripwire())
    import content.bio  # noqa: F401
    from core.summarize.budget_summary import maybe_summarize, tier2_diag

    msgs = _big_msgs(5)   # ~2.5k chars
    out  = maybe_summarize("thr_low", msgs, budget_chars=25_000)
    assert out == msgs
    assert call_count["n"] == 0
    assert tier2_diag()["calls"] == 0


# ── 6. Live-session footprint regression ───────────────────────────
def test_lean_budget_low_enough_to_actually_fire():
    """The shipped lean_guide.yaml must have summary_budget_chars set
    LOW enough that real sessions trigger Tier-2 — otherwise lean's
    whole rationale is broken."""
    import content.bio  # noqa: F401
    from core.runtime.agent import get_agent_spec
    lean = get_agent_spec("lean_guide")
    assert lean is not None
    b = lean.summary_budget_chars
    assert b is not None
    # Sanity bound: well below a 40,960-token window times 4 chars/tok.
    # If someone bumps it past ~50k they've defeated the point.
    assert 5_000 <= b <= 50_000, (
        f"lean summary_budget_chars={b} is outside the actionable "
        "range for a small-context backend")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
