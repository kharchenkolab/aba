"""An app and its base packs are ONE tested pair; promote must move both.

`deploy.sh promote` copies the app + SIF and nothing else. On 2026-08-26 that
would have put an August release onto July packs: the base the release DECLARES
would not match the one published, so every production user would have solved
and built a private base instead of adopting the prebuilt image — and the
libraries that are free in the August pack would have cost an install each.

Nothing in the command said so. The only way to see it was to diff two
catalog.json files by hand. So promote now refuses, and the publish step gained
the pointer lever that makes a safe ordering possible at all.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
VBC = REPO.parent / "aba-vbc"


def _deploy() -> str:
    if not (VBC / "deploy.sh").exists():
        pytest.skip("aba-vbc checkout not alongside this one")
    return (VBC / "deploy.sh").read_text()


def test_promote_checks_pack_parity_before_moving_bytes():
    s = _deploy()
    assert "check_pack_parity" in s
    body = s[s.index("do_promote() {"):]
    body = body[:body.index("\n}\n")]
    assert "check_pack_parity" in body, "promote does not run the check"
    # and it must run BEFORE any bytes move
    assert body.index("check_pack_parity") < body.index('install -d "$APP_ROOT"'), (
        "the parity check runs after the promote has already copied")


def test_the_refusal_is_fail_closed():
    """A warning that scrolls past is how this shipped for a day.

    TWO refusal tiers, and the distinction is load-bearing:
      rc=2  the promote CANNOT complete (the pointer flip has no tool). Never
            overridable — forcing past it lands the half-applied state.
      rc=1  the packs differ. A judgement call an operator may override.
    """
    s = _deploy()
    blk = s[s.index("check_pack_parity \"$STAGE_SHARE/envs\""):]
    blk = blk[:blk.index("local vstamp")]
    assert '"$_pp" = 2' in blk and "die " in blk, (
        f"pre-flight failure must die unconditionally: {blk}")
    # and the overridable tier still dies without --yes
    assert "ASSUME_YES" in blk and blk.count("die ") >= 2, blk


def test_the_unoverridable_tier_is_not_gated_on_yes():
    """`--yes` skips CONFIRMATIONS. It must not skip a pre-flight that says the
    next step cannot run — it did, and walked into the broken state."""
    s = _deploy()
    blk = s[s.index("check_pack_parity \"$STAGE_SHARE/envs\""):]
    blk = blk[:blk.index("local vstamp")]
    hard = blk[blk.index('"$_pp" = 2'):]
    hard = hard[:hard.index("\n")]
    assert "ASSUME_YES" not in hard, hard


def test_the_refusal_names_the_fix_and_the_ORDERING():
    """The dangerous part is not publishing — it is publishing with the
    pointer. Consumers adopt `latest`, so a plain publish changes what the
    CURRENTLY DEPLOYED app resolves, before that app has been replaced."""
    s = _deploy()
    assert "--no-latest" in s, "the refusal must name the pointer-safe publish"
    assert "publish_base_packs.py" in s


def test_the_publish_script_actually_has_that_lever():
    """Advice naming a flag the tool refuses is worse than no advice."""
    src = (REPO / "scripts" / "publish_base_packs.py").read_text()
    assert '"--no-latest"' in src
    assert "latest=not args.no_latest" in src


def test_the_helper_threads_the_flag_to_the_substrate():
    """weft's env_publish defaults latest=True; a helper that drops the
    parameter silently re-arms the hazard."""
    src = (REPO / "backend" / "core" / "compute" / "seeding.py").read_text()
    fn = src[src.index("def publish_base_packs("):]
    fn = fn[:fn.index("\ndef ")]
    assert "latest: bool = True" in fn
    assert "latest=latest" in fn, "the flag never reaches env_publish"
