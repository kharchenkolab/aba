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


def test_promote_runs_the_pack_gate_before_moving_bytes():
    """The gate must run before the app is copied, whatever the gate IS.

    Anchored on the CALL SITE, not on a function name or a byte window: the
    gate has already been rewritten once (two published trees -> one store
    with per-deployment pins) and the three assertions here that named
    `check_pack_parity` all broke, hiding whether promote still checked
    anything at all."""
    s = _deploy()
    body = s[s.index("do_promote() {"):]
    body = body[:body.index("\n}\n")]
    gates = [g for g in ("check_pack_pins", "check_pack_parity") if g in body]
    assert gates, "promote runs NO pack gate at all"
    first = min(body.index(g) for g in gates)
    assert first < body.index('install -d "$APP_ROOT"'), (
        "the pack gate runs after the promote has already copied bytes")


def test_both_refusal_tiers_survive_in_promote():
    """TWO tiers, and the distinction is load-bearing:
         rc=2  the promote CANNOT complete — never overridable, because
               forcing past it lands the half-applied state.
         rc=1  a judgement call an operator may override with --yes.
    """
    s = _deploy()
    body = s[s.index("do_promote() {"):]
    body = body[:body.index("\n}\n")]
    blk = body[body.index("_pp=$?"):body.index("local vstamp")]
    assert '"$_pp" = 2' in blk and "die " in blk, (
        f"pre-flight failure must die unconditionally: {blk}")
    assert "ASSUME_YES" in blk and blk.count("die ") >= 2, blk


def test_the_unoverridable_tier_is_not_gated_on_yes():
    """`--yes` skips CONFIRMATIONS. It must not skip a pre-flight that says the
    next step cannot run — it did once, and walked into the broken state."""
    s = _deploy()
    body = s[s.index("do_promote() {"):]
    blk = body[body.index("_pp=$?"):body.index("local vstamp")]
    hard = blk[blk.index('"$_pp" = 2'):]
    hard = hard[:hard.index("\n")]
    assert "ASSUME_YES" not in hard, hard


def test_the_gate_logic_itself_is_tested_behaviourally():
    """The bash is a doorway; the decisions live in python so they can be
    driven directly. Every gate that mattered on 2026-08-27 was a source-grep
    over deploy.sh, which is why none of them fired."""
    assert (REPO / "scripts" / "check_pack_pins.py").exists()
    assert (REPO / "tests" / "test_promote_pin_gate.py").exists()


def test_a_pack_is_never_copied_between_trees():
    """A squashfs pack bakes its own absolute prefix, so a copy activates only
    at the path it was built for. Copies carrying staging paths killed every
    session in production AND — via the shared ro_roots, where adoption
    resolves an EnvID across all roots — in staging too. The publish verb must
    BUILD in the destination."""
    s = _deploy()
    body = s[s.index("do_publish_packs() {"):]
    body = body[:body.index("\n}\n")]
    assert "--from-tree" not in body, (
        "publish-packs mirrors from another tree — that is the copy that "
        "produced an image which only activates at the source path")
    assert '--tree "$ENVS"' in body


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
