"""Guards for the register-the-subject-data rule and the guard that tests it.

The rule itself is behavioural — whether an agent actually registers the right
things is asserted by the live scenario `regtest/scenarios/dataset_registration`
(two-sided: the acquisition yields exactly ONE dataset, the lookup yields none).

What can't be left to that scenario is whether the rule TEXT reaches the model at
all. It nearly didn't: `behavior.md` is swapped for `behavior_slim.md` on the lean
tiers (build.py `_behavior_block`), and the slim variant carries no curation
section — so the first version of this rule rendered on `full`/`standard` and was
silently absent on `lean`/`lean_small`. A rule that doesn't render is a no-op that
no behavioural test on the standard tier can detect.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio

TOOLS = [{"name": "run_python"}, {"name": "register_dataset"},
         {"name": "present_plan"}, {"name": "Skill"}]

# the load-bearing clauses: the obligation, and the SET clause that keeps a
# multi-unit acquisition from becoming one entity per file
_OBLIGATION = "Register the data an analysis is ABOUT"
_SET_CLAUSE = "SET"


@pytest.mark.parametrize("mode", ["full", "standard", "lean", "lean_small"])
def test_registration_rule_renders_in_every_tier(mode):
    from content.bio.prompts.build import build_system
    stable, _dyn = build_system(TOOLS, role="primary", intent="analyze", ctx={}, mode=mode)
    assert _OBLIGATION in stable, (
        f"the register-the-subject-data obligation is missing from the {mode} system "
        f"prompt — tiers that swap in a slim behaviour file must carry it too")
    assert _SET_CLAUSE in stable, (
        f"{mode}: the 'acquired as a SET -> ONE dataset' clause is missing; without it "
        f"a multi-unit acquisition becomes one entity per file")


def test_registration_rule_names_the_tool():
    """The rule has to name `register_dataset`, not gesture at 'registering' —
    the tool is MCP-served and does not appear in TOOL_SCHEMAS, so a rule that
    only says 'register it' leaves the agent nothing to call."""
    from content.bio.prompts.build import build_system
    stable, _ = build_system(TOOLS, role="primary", intent="analyze", ctx={}, mode="full")
    assert "register_dataset" in stable


def test_entities_of_type_max_can_fail():
    """The scenario's ceiling check must actually be able to fail. `entities_of_type`
    is a >= assertion, so on its own it rewards registering MORE — a threshold rule
    guarded only by it passes when the agent registers everything, which is the
    entity-noise failure the curation rule exists to prevent."""
    sys.path.insert(0, str(ROOT / "regtest" / "harness"))
    from runner import entity_type_bounds   # noqa: E402

    ents = [{"type": "dataset", "status": "active", "id": "d1"},
            {"type": "dataset", "status": "active", "id": "d2"}]
    over = entity_type_bounds({"entities_of_type_max": {"dataset": 1}}, ents)
    assert over, "two datasets under a max of 1 must FAIL"
    assert "entities_of_type_max" in over[0]

    assert entity_type_bounds({"entities_of_type_max": {"dataset": 2}}, ents) == []
    # both bounds together — the shape the scenario actually uses
    assert entity_type_bounds(
        {"entities_of_type": {"dataset": 1}, "entities_of_type_max": {"dataset": 1}},
        [ents[0]]) == []
    # zero registered fails the FLOOR (the live provenance gap this rule is for)
    assert entity_type_bounds({"entities_of_type": {"dataset": 1}}, [])

    # archived entities count for neither bound
    ents2 = [{"type": "dataset", "status": "active", "id": "d1"},
             {"type": "dataset", "status": "archived", "id": "d2"}]
    assert entity_type_bounds({"entities_of_type_max": {"dataset": 1}}, ents2) == []


def test_cache_hit_check_threshold_and_arming():
    """cache_hit_min ctx check: threshold on the turn's hit fraction, ARMED —
    requested-but-unmeasured is a loud failure, never a silent pass."""
    sys.path.insert(0, str(ROOT / "regtest" / "harness"))
    from runner import cache_hit_check   # noqa: E402
    # not requested → no-op regardless of usage
    assert cache_hit_check({}, None) == []
    # healthy warm turn passes
    ok = cache_hit_check({"cache_hit_min": 0.8},
                         {"cache_read": 9000, "cache_write": 500, "input": 400})
    assert ok == []
    # regression fails with the numbers in the message
    bad = cache_hit_check({"cache_hit_min": 0.8},
                          {"cache_read": 5000, "cache_write": 4000, "input": 1000})
    assert bad and "caching regression" in bad[0] and "0.500" in bad[0]
    # ARMED: requested but nothing measured → loud distinct failure
    unmeasured = cache_hit_check({"cache_hit_min": 0.8}, None)
    assert unmeasured and "UNMEASURED" in unmeasured[0]
    assert cache_hit_check({"cache_hit_min": 0.8}, {"cache_read": 0}) \
        and "UNMEASURED" in cache_hit_check({"cache_hit_min": 0.8}, {})[0]


@pytest.mark.parametrize("mode", ["full", "standard", "lean", "lean_small"])
def test_registration_forbids_the_offer_and_names_the_trigger(mode):
    """The rule was DELIVERED, quoted back verbatim by the agent, and still lost
    (live 2026-07-21): it produced the curation bullet's offer template with
    "Register" swapped in — "Want me to register these as a Dataset entity?" —
    and waited for the user.

    Two causes, two assertions. (1) The offer must be forbidden IN THE SAME
    BREATH as the offer template is taught, or the concrete pattern beats the
    abstract permission that follows it. (2) The trigger must be the data
    LANDING, not "before analyzing" — the failure happened on a download-and-stop
    turn where no analysis was pending, so that cue never fired."""
    from content.bio.prompts.build import build_system
    stable, _ = build_system(TOOLS, role="primary", intent="fetch data", ctx={}, mode=mode)
    low = stable.lower()
    assert "offer" in low and "register" in low
    # (1) an explicit prohibition on offering to register
    assert ("never offer to register" in low) or ("register it yourself, never offer" in low), (
        f"{mode}: nothing forbids OFFERING to register — the observed failure")
    # (2) the landing trigger, not an analysis-time cue
    assert "the moment the data lands" in low, (
        f"{mode}: the registration trigger is not the data landing")


def test_registration_rule_precedes_the_curation_prohibition():
    """Order is load-bearing: the curation bullet supplies a concrete offer
    TEMPLATE, and a permission that follows a template gets pattern-matched away.
    Registration must come first."""
    from pathlib import Path
    body = (Path(__file__).resolve().parents[1]
            / "backend/system_bundle/rules/behavior.md").read_text()
    reg = body.index("Register the data an analysis is ABOUT")
    cur = body.index("Curation is the USER's gesture")
    assert reg < cur, (
        "the curation prohibition precedes the registration rule again — the "
        "offer template will out-compete it")
