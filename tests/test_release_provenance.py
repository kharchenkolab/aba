"""A release must record what it was BUILT FROM, not only which bytes it is.

Live, 2026-08-25. `WEFT_REF=main` floats and build.sh re-clones the substrate on
every build, so the compute substrate under a release is whatever weft's main
happened to be at build time. A release shipped carrying a substrate 64 commits
newer than the one anyone had reviewed — a change that included a new hard
refusal (post-link scripts), a new activation contract, and a fix to snapshots
on the deployment shape we ship. It went out unnoticed because the manifest
records the aba version and a content id for the image, and a content id
addresses BYTES; it does not say what those bytes were built from.

Two consequences, both of which bit:
  * you cannot roll back to a revision you never wrote down;
  * you cannot attribute a behaviour change to a substrate you cannot name —
    every surprise gets debugged against the wrong repo first.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def test_manifest_carries_provenance(tmp_path, monkeypatch):
    """THE regression: what went in is written down."""
    from core import release
    monkeypatch.setenv("ABA_SHARE", str(tmp_path))
    (tmp_path / "components" / "sif" / "cid1").mkdir(parents=True)
    release.compose_release("v1", {"sif": "cid1"}, share=str(tmp_path),
                            provenance={"weft": "9c339f1", "aba": "f171694b"})
    mf = json.loads((tmp_path / "releases" / "v1" / "manifest.json").read_text())
    assert mf["provenance"] == {"weft": "9c339f1", "aba": "f171694b"}, mf


def test_absent_provenance_writes_no_empty_key(tmp_path):
    """WIDE: an older/other caller must not gain a misleading empty field —
    'provenance: {}' would read as 'nothing went in', which is worse than the
    key being absent."""
    from core import release
    (tmp_path / "components" / "sif" / "cid1").mkdir(parents=True)
    release.compose_release("v2", {"sif": "cid1"}, share=str(tmp_path))
    mf = json.loads((tmp_path / "releases" / "v2" / "manifest.json").read_text())
    assert "provenance" not in mf, mf
    release.compose_release("v3", {"sif": "cid1"}, share=str(tmp_path),
                            provenance={"weft": ""})
    mf3 = json.loads((tmp_path / "releases" / "v3" / "manifest.json").read_text())
    assert mf3.get("provenance", {}) == {}, mf3


def test_deploy_records_the_substrate_revision():
    """The deploy script must actually PASS it — a parameter nothing supplies
    is a field that stays empty in production while the test passes."""
    dep = REPO.parent / "aba-vbc" / "deploy.sh"
    if not dep.exists():
        pytest.skip("aba-vbc checkout not present")
    src = dep.read_text()
    assert "--provenance" in src and "weft=" in src, (
        "deploy.sh stages a release without recording which substrate went in")
