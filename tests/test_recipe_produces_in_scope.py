"""A bound recipe's own outputs are IN scope at the plan's end (regression 2026-07-12).

The scope guardrail used to list "the recipe" among the out-of-scope "natural next
thing" sources. That made a recipe's own closing gesture — writing the viewer store
and offering the get_viewer_url link — read as scope creep, so the agent hit "STOP
after the last plan step" and never offered the viewer (observed live in thr_f09fa941:
the link appeared only after the user asked). The rule now carves out that a bound
recipe's canonical `produces` / result-delivery steps belong to the plan.

Guards the SHARED agent input (behavior.md family): the carve-out must be present in
the composed primary prompt, and the stale "recipe = out-of-scope" framing must be gone.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_scope_rule_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"),
    "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "artifacts"),
    "ABA_WORK_DIR": str(Path(_tmp) / "work"),
    "DATA_DIR": str(Path(_tmp) / "data"),
})
sys.path.insert(0, str(ROOT / "backend"))

RULES = ROOT / "backend" / "system_bundle" / "rules"
SCOPE_FILES = [RULES / "behavior.md", RULES / "behavior_slim.md",
               RULES / "required" / "plan_first.md"]


def test_carveout_present_in_every_scope_rule_file():
    for f in SCOPE_FILES:
        txt = f.read_text()
        assert "part of the plan's contract" in txt or 'not "extra work"' in txt, \
            f"{f.name}: missing the recipe-produces carve-out"
        assert "get_viewer_url" in txt or "viewer store" in txt or "result-delivery" in txt, \
            f"{f.name}: carve-out doesn't name the produces/delivery gesture"


def test_recipe_no_longer_framed_as_out_of_scope():
    # the exact stale phrasing that classified recipe-suggested work as creep
    for f in (RULES / "behavior.md", RULES / "behavior_slim.md"):
        txt = f.read_text()
        assert "the broader dataset, the recipe, or your training prior" not in txt, \
            f"{f.name}: still lists 'the recipe' as an out-of-scope creep source"


def test_carveout_reaches_the_composed_primary_prompt():
    from core.graph._schema import init_db
    init_db()
    import content.bio  # noqa: F401 — registers bundle
    from content.bio.prompts.build import build_system
    out = build_system([], role="primary")
    prompt = out if isinstance(out, str) else out[0]
    # the plan-scope rule is in the primary prompt, now carrying the carve-out
    assert "part of the plan's contract" in prompt or "not \"extra work\"" in prompt, \
        "the recipe-produces carve-out did not reach the composed primary prompt"
