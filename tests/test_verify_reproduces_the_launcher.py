"""The verification harness must boot the server the way the LAUNCHER does.

`ABA_SHARE` is documented as "shared install tree for immutable releases", and
its one consumer is `release.share_root()`, which expects to find `releases/`
and `current` directly inside it. The OOD launcher therefore sets it to the
release root — `<share>/app`. `verify.sh` set it to `<share>`, one level up.

So under verify, `resolve_current()` found nothing: no release id, no
pin-on-launch, no provenance. Every release-resolution behaviour was untested
by the gate that exists to test the release — and the failure is silent,
because "no release layout" is a legitimate state (a personal install has
none), so the code degrades politely instead of complaining.

Found 2026-08-26, when a newly added `/api/health` provenance field failed to
appear during verify. The field was correct; the harness was booting a
configuration production never runs. Same lesson as the incidents this suite is
full of — deployment shape is a fixture dimension — this time in the instrument.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "install" / "ood" / "aba" / "template" / "script.sh.erb"
# The forwarding itself moved into the contract both launchers source.
CONTRACT = REPO / "install" / "ood" / "aba" / "template" / "aba_launch.sh"
VERIFY = REPO.parent / "aba-vbc" / "verify.sh"


def _env_assignments(text: str, var: str) -> "list[str]":
    """Every value the script gives VAR on its way into the container.

    Two forms now, and both must be seen. verify.sh used to pass ABA_SHARE with
    an explicit `--env`; it now `export`s it and lets the shared launch contract
    (install/ood/aba/template/aba_launch.sh) forward it. A guard that still
    matched only `--env` would find nothing, and "nothing" reads as a pass in a
    loop over matches — the empty-subject-set failure, in the instrument that
    exists to catch this exact regression. So: match both, and require at least
    one, which the caller already asserts."""
    return (re.findall(rf'--env\s+{var}="?([^"\s\\]+)"?', text)
            + re.findall(rf'^\s*export\s+{var}="?([^"\s\\]+)"?', text, re.M))


def test_verify_passes_the_release_root_as_share():
    """THE regression: ABA_SHARE must name the directory holding releases/."""
    if not VERIFY.exists():
        pytest.skip("aba-vbc checkout not present")
    got = _env_assignments(VERIFY.read_text(), "ABA_SHARE")
    assert got, "verify.sh must forward ABA_SHARE at all"
    for v in got:
        assert v.rstrip("/").endswith("APP") or "/app" in v or v == "$APP", (
            f"verify.sh forwards ABA_SHARE={v!r}. release.share_root() expects the "
            f"RELEASE ROOT (the dir containing releases/ and current), which the OOD "
            f"launcher gives as <share>/app. Passing the share root makes "
            f"resolve_current() find nothing, silently: no release id, no "
            f"pin-on-launch, no provenance — and 'no release layout' is a legitimate "
            f"state, so nothing complains.")


def test_the_launcher_is_the_reference_and_still_says_app():
    """If the launcher's convention ever changes, this test must fail rather
    than let verify quietly diverge again."""
    if not LAUNCHER.exists():
        pytest.skip("launcher template not present")
    text = LAUNCHER.read_text() + "\n" + CONTRACT.read_text()
    assert "ABA_SHARE" in text, "the launcher must forward ABA_SHARE"
    # the launcher derives the release root; assert the concept is still there
    assert "release" in text.lower()


def test_share_root_wants_the_dir_that_holds_releases(tmp_path):
    """Pin the meaning itself, so the assertion above is not just folklore."""
    import sys
    sys.path.insert(0, str(REPO / "backend"))
    from core import release
    (tmp_path / "releases" / "v1").mkdir(parents=True)
    assert release._releases(release.share_root(str(tmp_path))) == tmp_path / "releases"
