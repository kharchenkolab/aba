"""Every spec aba composes must acknowledge the post-link scripts it pulls.

Some conda packages ship as a few KB of scripts whose real payload a POST-LINK
script downloads. The packer stages those scripts and never runs them, so the
package records as installed while its payload does not exist. The substrate now
refuses such an env outright (`env.post_link_scripts`, `retryable=false`) rather
than hand back one that is silently broken — the right call, and the reason
DESeq2 could not load in a published pack for weeks.

The acknowledgment went into the base PACK. It did not travel to the specs aba
composes itself: `make_isolated_env` built R envs with zero `post_install` steps
while still pulling the same dependency, so every isolated R env that reached
GenomeInfoDb failed to realize. Live 2026-08-26 — three of one project's five
named envs, each a separate user request that ended in a hard refusal.

The lesson is the recurring one: a fix applied at one composition site is not a
fix. Both places that build an R spec must carry it, and this file is what keeps
them together.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

PACK = REPO / "install" / "core" / "envs" / "r_bio.yaml"


def test_isolated_r_envs_carry_the_acknowledgment():
    """THE regression."""
    from core.compute.named_envs import _spec_for
    spec = _spec_for("p", "e", "r", ["bioconductor-genomeinfodb"])
    steps = spec.get("post_install") or []
    assert steps, "an isolated R spec must carry the post-link acknowledgment"
    body = "\n".join(steps)
    assert "genomeinfodbdata-post-link.sh" in body, body
    assert "rm -f" in body, (
        "consuming the staged script IS the acknowledgment — the substrate "
        "checks for its absence, so the removal is load-bearing")


def test_the_step_is_a_no_op_when_the_package_is_absent():
    """WIDE: most isolated R envs never pull it, and must pay nothing.

    An unconditional source install would slow every R env and could fail on
    envs with no R at all in the closure."""
    from core.compute.named_envs import _spec_for
    body = "\n".join(_spec_for("p", "e", "r", ["r-ggplot2"])["post_install"])
    assert "|| exit 0" in body, (
        "the step must exit cleanly when the staged script is not there")
    # the guard must come BEFORE the install, or the no-op costs a download
    assert body.index("|| exit 0") < body.index("install.packages"), body


def test_python_envs_get_no_r_step():
    """WIDE: the acknowledgment is R-specific; a python env must not run R."""
    from core.compute.named_envs import _spec_for
    assert "post_install" not in _spec_for("p", "e", "python", ["numpy"])


def test_the_pinned_payload_matches_the_base_pack():
    """ONE version, two composition sites. A drift here ships two different
    GenomeInfoDbData builds depending on which door the user came through —
    and an EnvID must mean one thing."""
    from core.compute.named_envs import _GENOMEINFODBDATA_VERSION
    assert PACK.exists(), PACK
    text = PACK.read_text()
    found = set(re.findall(r"GenomeInfoDbData_([0-9.]+?)\.tar\.gz", text))
    assert found, "the base pack no longer pins GenomeInfoDbData — re-read this guard"
    assert found == {_GENOMEINFODBDATA_VERSION}, (
        f"base pack pins {found}, named_envs pins "
        f"{_GENOMEINFODBDATA_VERSION!r} — the two composition sites have drifted")


def test_the_payload_is_pinned_not_latest():
    """A post-link script downloads UNPINNED content, which is why the
    substrate refuses to run it: an EnvID would mean different bytes on
    different days. Our replacement must not reintroduce that."""
    from core.compute.named_envs import _GENOMEINFODBDATA_URL
    assert "latest" not in _GENOMEINFODBDATA_URL.lower(), _GENOMEINFODBDATA_URL
    assert re.search(r"_\d+\.\d+\.\d+\.tar\.gz$", _GENOMEINFODBDATA_URL), \
        _GENOMEINFODBDATA_URL
