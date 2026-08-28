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


def _promote_body() -> str:
    if not DEPLOY.exists():
        pytest.skip("aba-vbc checkout not present")
    src = DEPLOY.read_text()
    m = re.search(r"^do_promote\(\) \{(.*?)^\}", src, re.S | re.M)
    assert m, "could not locate do_promote() in deploy.sh"
    return _code_only(m.group(1))


def test_promote_drives_the_target():
    body = _promote_body()
    assert "verify.sh" in body, (
        "promote never drives the target — it ends at a structural selfcheck, "
        "which is what let a fully-broken production pass every assertion")
    assert "--lanes" in body, "the drive must run lanes, not just boot the image"


def test_the_drive_happens_before_the_card_is_published():
    """THE ordering property. publish_card is what exposes users."""
    body = _promote_body()
    drive = body.index("verify.sh")
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
    an older release — a mismatch outliving the failed promote."""
    body = _promote_body()
    drive = body.index("verify.sh")
    tail = body[drive:]
    assert "rollback" in tail, "a failed drive does not roll back"
    fail_block = tail[:tail.index("die ")] if "die " in tail else tail
    assert "stage_site_artifacts" in fail_block, (
        "rollback does not restore the previous release's site config")
    assert "publish_card" in fail_block, (
        "rollback does not republish the OLD card")


def test_a_failed_rollback_is_reported_not_swallowed():
    """The worst case — drive failed AND rollback failed — must be loud, because
    the target is then live on an unverified release."""
    body = _promote_body()
    tail = body[body.index("verify.sh"):]
    assert re.search(r"ROLLBACK FAILED|rollback FAILED", tail), (
        "a failed rollback is not surfaced; the operator would be told the "
        "promote aborted while the target is still on the new release")


def test_the_skip_switch_announces_itself():
    """An escape hatch is fine; a silent one is not — the whole defect was a
    promote that looked verified and wasn't."""
    body = _promote_body()
    m = re.search(r"PROMOTE_NO_DRIVE", body)
    assert m, "no documented way to skip the drive (an operator will need one)"
    after = body[m.start():]
    assert re.search(r"NOT DRIVEN|not driven", after), (
        "skipping the drive prints nothing — a promote that skipped its only "
        "behavioural check must say so")
