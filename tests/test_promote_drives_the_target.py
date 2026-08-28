"""A promote must DRIVE the target before it exposes anyone to it.

WHAT THIS GUARDS. `do_promote` used to end at `do_selfcheck`, which is
structural — it asks whether paths and pointers are coherent and never sends a
prompt. On 2026-08-27 every one of those assertions held while every kernel in
production was dying: the env packs were fine at staging's paths and dead at
production's. A human opening a session was the detector.

Production is not staging with a different name. It has its own share root,
card, pins and pack resolution, so "staging was green" is evidence about
staging. The only claim worth making after a promote is that the target
answered a real prompt at its OWN paths.

ORDER IS PART OF THE PROPERTY. `publish_card` — the step that exposes users —
came BEFORE the only check. Publishing first makes the check partly decorative,
so this asserts the drive precedes the card, not merely that it exists.

Why a text property and not an execution test: deploy.sh is shell in a private
deployment repo with no harness, and executing a promote means moving a real
deployment. The behaviours below are cheap to state over the source and would
each have caught the live regression.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

DEPLOY = Path(__file__).resolve().parents[2] / "aba-vbc" / "deploy.sh"


def _code_only(body: str) -> str:
    """The body with comment lines blanked (length preserved, so offsets still
    compare). Without this the guard matched identifiers inside its own
    explanatory comments and reported a violation that was prose."""
    out = []
    for line in body.splitlines(keepends=True):
        out.append(" " * (len(line) - 1) + "\n" if line.lstrip().startswith("#") else line)
    return "".join(out)


def _deploy_src() -> str:
    if not DEPLOY.exists():
        pytest.skip("aba-vbc checkout not present")
    return DEPLOY.read_text()


def _promote_body() -> str:
    """The body of the one deployment sequence. `promote` stopped being its own
    function: it and `stage` were the same nine steps written twice, in
    different orders, and every incident this file records was a divergence
    between them. They are now shorthands over `do_apply`, which is what these
    properties are about."""
    if not DEPLOY.exists():
        pytest.skip("aba-vbc checkout not present")
    src = DEPLOY.read_text()
    # The sequence AND the two helpers it delegates to. `_drive` names the one
    # place that knows how to send a deployment real prompts; `_apply_rollback`
    # the one place that puts a deployment back. Reading only do_apply() would
    # score the drive as absent the moment it was given a name — a guard keyed
    # to an inline call expires the first time the code is tidied.
    out = []
    for fn in ("do_apply", "_drive", "_apply_rollback"):
        m = re.search(rf"^{fn}\(\) \{{(.*?)^\}}", src, re.S | re.M)
        assert m, f"could not locate {fn}() in deploy.sh"
        out.append(_code_only(m.group(1)))
    # do_apply first, so offset comparisons below are within the sequence and the
    # helpers' bodies sort after it (a publish_card inside _apply_rollback must
    # not read as "published early").
    return "\n".join(out)


def test_promote_drives_the_target():
    body = _promote_body()
    assert "verify.sh" in body, (
        "promote never drives the target — it ends at a structural selfcheck, "
        "which is what let a fully-broken production pass every assertion")
    assert "--lanes" in body, "the drive must run lanes, not just boot the image"


def test_the_drive_happens_before_the_card_is_published():
    """THE ordering property. publish_card is what exposes users."""
    body = _promote_body()
    drive = body.index("_drive ")
    cards = [m.start() for m in re.finditer(r"publish_card", body)]
    assert cards, "promote no longer publishes a card at all"
    # The property is NOTHING EXPOSES USERS BEFORE THE DRIVE — not merely "a
    # publish_card exists after it". The weaker form was satisfied by the
    # publish_card inside the rollback branch, so moving the real one earlier
    # still passed. Assert the absence, which is what the ordering means.
    early = [c for c in cards if c < drive]
    assert not early, (
        f"publish_card runs BEFORE the drive (at offset(s) {early}) — users get "
        f"the new card before anything has confirmed the release answers a "
        f"prompt, which makes the check partly decorative")
    assert [c for c in cards if c > drive], (
        "no publish_card after the drive — the promote would never expose the "
        "new card at all")


def test_a_failed_drive_rolls_back_and_restores_the_old_card():
    """A failed drive must leave users on what they had. Rolling back the
    release but NOT republishing the card would leave the new card pointing at
    an older release — a mismatch outliving the failed apply.

    Two halves, because the restore now has a name: the failure path must CALL
    the rollback, and the rollback must restore all three things (bytes, config,
    card). Asserting only over a slice of the failure branch scored the restore
    as missing as soon as it moved into a function."""
    src = _deploy_src()
    seq = re.search(r"^do_apply\(\) \{(.*?)^\}", src, re.S | re.M).group(1)
    tail = seq[seq.index("_drive "):]
    fail_block = tail[:tail.index("die ")] if "die " in tail else tail
    assert "_apply_rollback" in fail_block, (
        "a failed drive does not roll back")

    rb = re.search(r"^_apply_rollback\(\) \{(.*?)^\}", src, re.S | re.M)
    assert rb, "no _apply_rollback() to restore the deployment"
    rb = _code_only(rb.group(1))
    assert "rollback" in rb, "the rollback does not move the release back"
    assert "stage_site_artifacts" in rb, (
        "rollback does not restore the previous release's site config")
    assert "publish_card" in rb, (
        "rollback does not republish the OLD card")


def test_a_failed_rollback_is_reported_not_swallowed():
    """The worst case — drive failed AND rollback failed — must be loud, because
    the target is then live on an unverified release."""
    body = _promote_body()
    tail = body[body.index("_drive "):]
    assert re.search(r"ROLLBACK FAILED|rollback FAILED", tail), (
        "a failed rollback is not surfaced; the operator would be told the "
        "promote aborted while the target is still on the new release")


def test_the_skip_switch_announces_itself():
    """An escape hatch is fine; a silent one is not — the whole defect was a
    promote that looked verified and wasn't."""
    body = _promote_body()
    m = re.search(r"APPLY_NO_DRIVE", body)
    assert m, "no documented way to skip the drive (an operator will need one)"
    after = body[m.start():]
    assert re.search(r"NOT DRIVEN|not driven", after), (
        "skipping the drive prints nothing — a promote that skipped its only "
        "behavioural check must say so")
