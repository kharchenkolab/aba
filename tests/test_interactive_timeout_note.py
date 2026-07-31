"""A timed-out cell must say something the agent can ACT on.

The interactive lane clamps `timeout_s` to a hard ceiling
(`run_exec.INTERACTIVE_MAX_S`). The old message was just
``Code execution timed out (1800s limit)`` — which reads like a tunable, so the
natural repair is to retry with a bigger `timeout_s`. That value is silently
clamped back to the same ceiling and the cell dies identically: a loop the agent
cannot see, because nothing anywhere says the limit is hard.

Live evidence for why this matters: in the placement study's clock scenarios the
agent is BIMODAL on a 3-hour job — when it engages the sizable-work protocol it
supplies cores/mem/runtime and backgrounds correctly, and when it doesn't it
emits a bare `run_python(code=…)` and runs inline. Nothing in that call
distinguishes it from a file-header check, so no router or tool guard can catch
it in advance. This message is the backstop for the case that slips through: it
cannot prevent the misclassification, but it caps the loss at the ceiling and
tells the agent the one thing that actually fixes it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from content.bio.tools.run_exec import INTERACTIVE_MAX_S, _timeout_note  # noqa: E402

pytestmark = pytest.mark.platform


def test_at_the_ceiling_it_names_the_hard_cap_and_the_background_lane():
    """The load-bearing case. Assert the FORBIDDEN advice is absent, not just
    that the right advice is present: 'raise timeout_s' here sends the agent
    into the clamp loop, and a message could easily contain both."""
    note = _timeout_note(INTERACTIVE_MAX_S)
    assert "background=True" in note, "the only real remedy is not named"
    assert "clamped" in note, "does not say a larger timeout_s is silently capped"
    assert "HARD" in note or "hard" in note, "does not say the ceiling is hard"
    # Forbidden action: telling the agent to raise timeout_s AT the ceiling.
    assert "raise timeout_s" not in note, (
        "advises raising timeout_s at the ceiling — that value is clamped back, "
        "so this sends the agent into an invisible retry loop")


def test_below_the_ceiling_raising_the_timeout_is_still_the_right_advice():
    """The other side. A cell that timed out at 60s is NOT at the cap, and the
    correct repair really is a bigger timeout_s — a message that always shouted
    'use background' would push short work into the job queue for nothing."""
    note = _timeout_note(60)
    assert "raise timeout_s" in note, "below the cap, raising the timeout IS the fix"
    assert "clamped" not in note, "wrongly claims the value would be clamped"
    assert str(INTERACTIVE_MAX_S) in note, "should say how far timeout_s can go"


def test_the_two_sides_differ():
    """WIDE: a note that ignored `timeout_s` would satisfy one of the tests above
    by accident. They must not be the same string."""
    assert _timeout_note(60) != _timeout_note(INTERACTIVE_MAX_S)


@pytest.mark.parametrize("over", [INTERACTIVE_MAX_S + 1, INTERACTIVE_MAX_S * 6])
def test_a_value_above_the_ceiling_is_treated_as_at_it(over):
    """DEGENERATE: callers pass the CLAMPED value today, but a caller that passed
    the requested one must not fall through to the 'just raise it' branch."""
    assert "background=True" in _timeout_note(over)
    assert "raise timeout_s" not in _timeout_note(over)


def test_the_interactive_paths_clamp_to_the_named_constant():
    """PROPERTY: the note is only true while the ceiling it cites is the one the
    code enforces. A bare 1800 reintroduced anywhere would drift them apart."""
    src = (Path(__file__).resolve().parents[1]
           / "backend/content/bio/tools/run_exec.py").read_text()
    assert "INTERACTIVE_MAX_S)" in src, "interactive paths no longer clamp to the constant"
    import re
    bare = re.findall(r"min\(int\(input_\.get\(\"timeout_s\"\)[^)]*\),\s*1800\)", src)
    assert not bare, f"a literal 1800 clamp bypasses INTERACTIVE_MAX_S: {bare}"
