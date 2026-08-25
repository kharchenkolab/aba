"""Health must say WHICH build is answering, not only that one is.

`/api/health` reported liveness and a degraded flag and never identified the
server. That is fine until two builds are in play — and two builds are always in
play during a rollout. A probe recording results across hours, an operator
reading a warning, a comparison of yesterday to today: each has to attribute
what it sees to a build, and each was left inferring it from a deploy log.

The substrate revision is the sharp case. It is not derivable from the release
id at all: `WEFT_REF` floated for months, so the same aba version shipped
different weft revisions, and on 2026-08-25 a 64-commit substrate change — a new
hard refusal, a new activation contract — reached users unnoticed because
nothing recorded or served the fact.

Liveness comes first: this must never raise. A personal install has no release
layout, and health answering 500 because provenance was unavailable would be a
strictly worse trade than not knowing the build.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def test_health_names_the_release_and_what_it_was_built_from(monkeypatch):
    """THE regression."""
    from core.web.routers import misc
    from core import release
    monkeypatch.setattr(release, "active_release_id", lambda: "2026.08.25-abc1234")
    monkeypatch.setattr(release, "read_manifest",
                        lambda ver, share=None: {"provenance": {"weft": "9c339f1",
                                                                "aba": "abc1234"}})
    got = misc._running_build()
    assert got["release"] == "2026.08.25-abc1234", got
    assert got["built_from"]["weft"] == "9c339f1", got


def test_no_release_layout_is_silent_not_broken(monkeypatch):
    """WIDE: a personal install has no releases; it must simply say nothing."""
    from core.web.routers import misc
    from core import release
    monkeypatch.setattr(release, "active_release_id", lambda: None)
    assert misc._running_build() == {}


def test_liveness_survives_a_broken_release_layout(monkeypatch):
    """ARMED: provenance is a nicety, liveness is not. An exception here must
    not reach the caller — health answering 500 because it could not name the
    build is worse than not naming it."""
    from core.web.routers import misc
    from core import release

    def _boom():
        raise RuntimeError("release tree is a symlink loop")

    monkeypatch.setattr(release, "active_release_id", _boom)
    assert misc._running_build() == {}


def test_health_includes_it(monkeypatch):
    """The field must reach the RESPONSE, not just exist as a helper — a
    provenance function nothing calls is the same as no provenance."""
    from core.web.routers import misc
    monkeypatch.setattr(misc, "_running_build", lambda: {"release": "r1"})
    body = misc.health()
    assert body["ok"] is True and body["release"] == "r1", body


def test_the_probe_stamps_every_row():
    """The consumer half: a results file spanning two builds with no per-row
    build is uninterpretable, which is how it would actually be read — as one
    run."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    assert "def running_build(" in src
    assert "**(build or {})" in src, (
        "rows must carry the build; stamping only the summary loses it exactly "
        "when a promotion happens mid-sweep")
