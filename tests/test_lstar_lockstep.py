"""Every pinned member of the lstar interchange set names the SAME version.

Five files in this repo pin a version of one interchange model — a store one
lane writes, another lane reads:

  * `install/core/envs/python_bio.yaml`   pypi `lstar-sc`   (writes stores)
  * `install/core/envs/r_bio.yaml`        cran `lstar`      (writes stores)
  * `install/core/install-lstar-r.sh`     `LSTAR_REF` tag   (writes stores, in
                                          the pack-less tools env: SIF images,
                                          legacy native installs)
  * `install/core/modules/install-viewer-pagoda3.sh`  the pagoda3 viewer dist
  * `install/sif/build.sh`                the same dist, baked into the image

A version they don't agree on is a silently wrong store, not a build error. That
stopped being theoretical at lstar 0.2.2: `validate()` now hard-errors on a
missing gene-major counts basis, and the pagoda3 dist checks the same
`provenance.viewer=basis` stamp client-side — so a set that straddles 0.2.2
produces stores half the estate refuses to open.

The hazard is old, and was being managed with PROSE: three of these files
already carried a "bump these together" comment, and two of the five were still
a release behind anyway. A comment cannot fail. This is the property instead.

Adding a sixth consumer is one row in LSTAR_MEMBERS or DIST_MEMBERS.

WHAT THIS FILE CANNOT CHECK, and how that is covered: the pagoda3 dist's own
lstar requirement lives in the pagoda3 repo, so no hermetic test can read it.
`test_the_dist_pin_is_the_REVIEWED_one` is a tripwire on exactly that — the dist
version is pinned to a constant here, so bumping the dist lands on this file and
the person bumping has to re-verify the pairing by hand. Verified for the current
set at v0.2.2: pagoda3 declares `lstar-sc>=0.2.2` (py/pyproject.toml) and
`lstar (>= 0.2.2)` (r/DESCRIPTION), both satisfied by the 0.2.2 pins.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]

# The dist release whose lstar requirement was checked by hand (see docstring).
# Bumping the dist means re-doing that check and moving this line.
REVIEWED_DIST = "0.2.2"


# ── extractors: each returns a bare version string, or None if UNPINNED ───────

def _yaml_exact_pin(path: Path, name: str) -> str | None:
    """The version a pack pins `name` to, or None if it is not pinned EXACTLY.

    Only `name ==X.Y.Z` counts. A bare name and a floor (`>=`) both return None
    on purpose: they are the shapes that let two lanes drift apart while every
    solve still succeeds, which is the whole failure this file exists to catch.
    (weft's cran lane accepts only `name` or `name ==X.Y.Z` anyway — a `>=`
    there is a task.invalid at solve time.)"""
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.startswith("- "):
            continue
        spec = line[2:].strip().strip('"').strip("'")
        parts = spec.split()
        if parts and parts[0] == name:
            rest = " ".join(parts[1:]).strip()
            return rest[2:].strip() if rest.startswith("==") else None
    return None


def _shell_tag_pin(path: Path, var: str) -> str | None:
    """A shell `VAR="vX.Y.Z"` assignment, normalized to a bare version."""
    m = re.search(rf'^{re.escape(var)}="v?([0-9][^"]*)"', path.read_text(), re.M)
    return m.group(1) if m else None


def _dist_url_versions(path: Path) -> tuple[str | None, str | None]:
    """(release tag version, zip asset version) from a pagoda3 dist URL."""
    text = path.read_text()
    tag = re.search(r"/releases/download/v([0-9][^/\s]*)/", text)
    asset = re.search(r"pagoda3-viewer-([0-9][^\s\"}]*)\.zip", text)
    return (tag.group(1) if tag else None, asset.group(1) if asset else None)


def _dist_pin(path: Path) -> str | None:
    tag, asset = _dist_url_versions(path)
    return tag if tag and tag == asset else None


PY_PACK = ROOT / "install" / "core" / "envs" / "python_bio.yaml"
R_PACK = ROOT / "install" / "core" / "envs" / "r_bio.yaml"
LSTAR_R_SH = ROOT / "install" / "core" / "install-lstar-r.sh"
VIEWER_SH = ROOT / "install" / "core" / "modules" / "install-viewer-pagoda3.sh"
SIF_SH = ROOT / "install" / "sif" / "build.sh"

# (path, what it pins, how to read it) — one row per member. ADD A ROW when a
# new file starts pinning lstar or the dist.
LSTAR_MEMBERS = [
    (PY_PACK, "pypi lstar-sc", lambda p: _yaml_exact_pin(p, "lstar-sc")),
    (R_PACK, "cran lstar", lambda p: _yaml_exact_pin(p, "lstar")),
    (LSTAR_R_SH, "LSTAR_REF tag", lambda p: _shell_tag_pin(p, "LSTAR_REF")),
]
DIST_MEMBERS = [
    (VIEWER_SH, "viewer-pagoda3 module", _dist_pin),
    (SIF_SH, "SIF baked dist", _dist_pin),
]
ALL_MEMBERS = LSTAR_MEMBERS + DIST_MEMBERS


# ── ARMED: an extractor that reads nothing blesses every arrangement ──────────

@pytest.mark.parametrize("path,what,read",
                         ALL_MEMBERS, ids=[m[1] for m in ALL_MEMBERS])
def test_every_member_yields_a_REAL_version(path, what, read):
    """A regex that matches nothing makes the equalities below vacuously true —
    the instrument-measures-nothing failure. Each member must produce a version.

    A None here means one of two things, both of which are the bug: the file
    stopped pinning (bare name / floor / mismatched URL halves), or it moved and
    this row now reads a file that no longer says anything."""
    assert path.exists(), f"{what}: {path} is gone — fix or drop the row"
    got = read(path)
    assert got, (
        f"{what} ({path.relative_to(ROOT)}) yields no exact version. Either the "
        f"pin was loosened — which is what lets the lanes drift — or this "
        f"extractor no longer matches the file")


# ── the degenerate shapes, on synthetic input ────────────────────────────────

def test_a_BARE_name_is_not_a_pin(tmp_path):
    """The shape this guard exists for: `- lstar` solves fine and bakes whatever
    the repo happens to serve that day."""
    p = tmp_path / "p.yaml"
    p.write_text("    cran:\n      - lstar\n")
    assert _yaml_exact_pin(p, "lstar") is None


def test_a_FLOOR_is_not_a_pin(tmp_path):
    """`>=` is the plausible near-miss: it looks like a pin and still drifts."""
    p = tmp_path / "p.yaml"
    p.write_text('    pypi:\n      - "lstar-sc >=0.2.2"\n')
    assert _yaml_exact_pin(p, "lstar-sc") is None


def test_a_commented_out_pin_does_not_count(tmp_path):
    """WIDE: these files are heavily commented, and a version quoted inside
    prose must not be mistaken for the live one."""
    p = tmp_path / "p.yaml"
    p.write_text("    pypi:\n      # - lstar-sc ==0.1.0   (the old pin)\n"
                 "      - lstar-sc ==0.2.2\n")
    assert _yaml_exact_pin(p, "lstar-sc") == "0.2.2"


def test_the_yaml_extractor_finds_a_pin_it_should(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("    cran:\n      - lstar ==1.2.3\n")
    assert _yaml_exact_pin(p, "lstar") == "1.2.3"


def test_the_shell_extractor_strips_the_v_and_reads_only_an_assignment(tmp_path):
    """The tag lane writes `v0.2.2` where the packs write `0.2.2`; they are the
    same version and must compare equal. A mention in prose is not a pin."""
    p = tmp_path / "s.sh"
    p.write_text('# LSTAR_REF="v9.9.9" in the old script\nLSTAR_REF="v1.2.3"\n')
    assert _shell_tag_pin(p, "LSTAR_REF") == "1.2.3"
    assert _shell_tag_pin(p, "NOPE_REF") is None


def test_a_half_edited_dist_URL_reads_as_UNPINNED(tmp_path):
    """The realistic half-edit: tag bumped, asset filename not (or the reverse).
    It 404s at install time on the user's machine, so it must fail here."""
    p = tmp_path / "d.sh"
    p.write_text('URL="https://x/releases/download/v0.2.2/pagoda3-viewer-0.2.1.zip"\n')
    assert _dist_pin(p) is None
    tag, asset = _dist_url_versions(p)
    assert (tag, asset) == ("0.2.2", "0.2.1")


# ── the property ─────────────────────────────────────────────────────────────

def test_BOTH_packs_still_DECLARE_lstar():
    """CEILING: deleting the dep from a pack would satisfy "they agree" by
    emptying one side. Presence is asserted separately from agreement — and
    without lstar the R lane simply cannot open a store."""
    assert "lstar-sc" in PY_PACK.read_text(), "the python pack dropped lstar-sc"
    assert re.search(r"^\s*- lstar\b", R_PACK.read_text(), re.M), \
        "the R pack dropped lstar"


def test_THE_PROPERTY_every_lstar_pin_names_the_same_version():
    """The three writing lanes produce the SAME store format. A set that
    straddles 0.2.2 means one lane writes stores another's validate() rejects."""
    seen = {what: read(p) for p, what, read in LSTAR_MEMBERS}
    assert len(set(seen.values())) == 1, (
        f"lstar pins disagree: {seen}. They write one interchange format — bump "
        f"every member together, and re-check the pagoda3 dist")


def test_THE_PROPERTY_every_dist_pin_names_the_same_release():
    """The module install and the SIF bake serve the same viewer. Two versions
    means the image and the native install disagree about the store contract —
    and only one of them gets reported when a user hits it."""
    seen = {what: read(p) for p, what, read in DIST_MEMBERS}
    assert len(set(seen.values())) == 1, (
        f"pagoda3 dist pins disagree: {seen}")


def test_the_dist_pin_is_the_REVIEWED_one():
    """TRIPWIRE. The dist's lstar requirement lives in the pagoda3 repo, so
    nothing here can verify the pairing automatically. Bumping the dist must
    therefore land on this line — the prompt to re-check that the new release's
    declared lstar is satisfied by the pins above."""
    got = _dist_pin(VIEWER_SH)
    assert got == REVIEWED_DIST, (
        f"the pagoda3 dist moved to v{got} but the reviewed pairing in this file "
        f"is still v{REVIEWED_DIST}. Check the new release's declared lstar "
        f"(py/pyproject.toml + r/DESCRIPTION in the pagoda3 repo) against the "
        f"pack pins, then update REVIEWED_DIST")


def test_the_R_pack_does_not_re_add_a_MOVING_cran_repo():
    """r_bio.yaml solved against live CRAN for a while, because weft's PPM
    snapshot lags UTC-today−2 and that lag hid a fresh release from an UNPINNED
    dep. The pin replaced that workaround. Re-adding a live repo would not cause
    skew now — weft asserts the `==` against whatever resolves, so it fails
    either way — but it makes WHEN the pack breaks depend on wall-clock: the day
    CRAN publishes rather than the day the snapshot catches up. Solve against the
    snapshot alone and keep the failure predictable."""
    live = [ln.strip() for ln in R_PACK.read_text().splitlines()
            if ln.split("#", 1)[0].strip().startswith("- http")
            and "cloud.r-project.org" in ln.split("#", 1)[0]]
    assert not live, (
        f"r_bio.yaml lists a moving CRAN repo again: {live}. The exact pin is "
        f"the lockstep mechanism now; see the comment above r_repositories")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
