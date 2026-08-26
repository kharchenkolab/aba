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
    """A warning that scrolls past is how this shipped for a day. Drift stops
    the promote."""
    s = _deploy()
    blk = s[s.index("if ! check_pack_parity"):]
    blk = blk[:blk.index("fi")]
    assert "die " in blk, "drift must abort, not warn"


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
