"""Everything in `system_bundle/rules/required/` must actually reach the prompt.

The directory is named `required`. It holds two files. Under the deployment's
default prompt arm only ONE of them renders: `plan_first.md` is ungated, while
`nonnegotiables.md` — the integrity invariants — is gated behind
`gate=lambda c: _is_nonneg()`, an A/B arm that production does not run. Its
own comment says so: "control = no-op".

Found 2026-08 from a field report. An agent described a data folder's contents
from the word "scMultiome" in its path and presented the inference as
observation; the user had to push back to get a real listing, and the folder
held one file matching nothing described. `nonnegotiables.md` says, in plain
words, "Facts come from the live source, not from memory, filenames, or the
project summary" — and it was not in the prompt that agent was running. The
rule existed, was correctly phrased, and was switched off.

An experiment arm that hides a rule is a reasonable thing to build. A `required`
rule silently absent from every production prompt is not, and nothing detected
it because the file was present, correct, and version-controlled.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
REQUIRED = REPO / "backend" / "system_bundle" / "rules" / "required"


def _distinctive_lines(md: Path, n: int = 3):
    """A few long, content-bearing lines — enough to identify the file's text
    inside a composed prompt without pinning its exact wording."""
    out = []
    for raw in md.read_text().splitlines():
        line = raw.strip().lstrip("-").strip()
        if len(line) >= 60 and not line.startswith("#"):
            out.append(line[:60])
        if len(out) >= n:
            break
    return out


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN AND OPEN: nonnegotiables.md is gated to the 'nonneg' prompt arm and "
    "production runs 'control', so the integrity invariants never render. NOT "
    "silently fixed here: the arm is a live A/B (hoist + de-bold + slim "
    "behavior.md together), so ungating just the hoist would both corrupt the "
    "experiment and duplicate the text it is measuring against. The specific "
    "hole this surfaced — inferring a folder's contents from its name — is "
    "closed in behavior.md, which is the arm production actually runs. "
    "strict=True: when the arm question IS settled, this fails until the "
    "xfail is removed, so the decision cannot be lost."))
def test_every_required_rule_renders_under_the_default_arm():
    from content.bio.prompts.build import build_system

    files = sorted(REQUIRED.glob("*.md"))
    assert files, "no required rules found — wrong path?"

    # Supply the tools the required rules declare they need (plan_first is
    # gated on `present_plan`), so the ONLY reason one can still be absent is
    # a role/arm gate — which is exactly what this test is about.
    tools = [{"name": "present_plan"}]
    stable, dynamic = build_system(tools, role="primary", intent="", ctx={})
    prompt = (stable or "") + "\n" + (dynamic or "")
    assert prompt.strip(), "composed an empty prompt — the check would be vacuous"

    missing = []
    for md in files:
        probes = _distinctive_lines(md)
        assert probes, f"{md.name}: no probe lines could be extracted"
        if not any(p in prompt for p in probes):
            missing.append(md.name)

    assert not missing, (
        "rule file(s) in required/ never reach the composed prompt under the "
        "default prompt arm — the rule exists, is correct, and is switched "
        f"off: {missing}")
