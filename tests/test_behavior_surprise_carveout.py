"""Guard the 'Surprises end the turn' rule's plan-execution carve-out.

Live incident (CBE-next pilot): mid an APPROVED scanpy plan, the agent hit a benign
intermediate result (a `rank_genes_groups` table sorted by raw score showed noise genes,
right before the step's log-fold-change / canonical-marker overlay — the correct read),
labelled it "Surprise", and end_turn'd — aborting the plan. Its own post-mortem: it
misapplied the "Surprises end the turn" guardrail, which as written fired on any surprise
and collided with the plan-scope rule ("the plan's steps ARE the scope … complete them").

The fix scopes the rule: a surprise the plan's OWN next step is meant to interpret is not a
stop signal — finish the step, then flag it. This is a STRUCTURAL regression guard (cheap,
runs everywhere) that the carve-out isn't silently dropped; the full BEHAVIORAL check is a
live-sweep scenario (agent runs a plan whose step N surfaces a benign surprise that step
N+1 resolves → must finish, not end_turn early) — added to regtest/scenarios when a
synthetic single-cell sample is wired in.
"""
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "backend" / "system_bundle" / "rules"


def _rule(fname: str) -> str:
    text = (RULES / fname).read_text()
    line = next((l for l in text.splitlines() if "Surprises end the turn" in l), "")
    assert line, f"{fname}: 'Surprises end the turn' rule missing entirely"
    return line.lower()


def test_surprise_rule_keeps_the_genuine_guardrail():
    """Both variants still STOP + end_turn on a genuine surprise (contradicts a reported
    claim / methods disagree / wrong annotation) — the carve-out must not gut the rule."""
    for f in ("behavior.md", "behavior_slim.md"):
        r = _rule(f)
        assert "stop" in r and "end_turn" in r, f"{f}: lost the STOP/end_turn guardrail"
        assert "already reported" in r, f"{f}: lost the 'contradicts what you already reported' trigger"


def test_surprise_rule_has_the_plan_next_step_carveout():
    """Both variants must carve out plan-internal surprises the plan's own next step
    resolves — else the rule re-aborts approved plans (the incident this fixes)."""
    for f in ("behavior.md", "behavior_slim.md"):
        r = _rule(f)
        assert "approved plan" in r, f"{f}: carve-out lost the 'approved plan' scope"
        assert "next step" in r, f"{f}: carve-out lost the 'plan's next step is the check' idea"
        assert "finish that step" in r, f"{f}: carve-out lost the 'finish that step' instruction"
