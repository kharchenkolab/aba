"""Who owns the conda spelling of an R package — and why it is not us.

conda spells R packages `r-<lowercase>` (the CRAN mirror) and
`bioconductor-<lowercase>` (the Bioconductor builds). A bare name cannot say
which registry it lives in.

weft derives the dialect (`spec.lane_spellings`) and, since bug5, returns BOTH
candidates in rank order, trying the second only on a not-found miss. Before
that it guessed `r-<name>` only, so every Bioconductor ask missed the conda
lane by construction and fell through to the SOURCE-only cran lane — eleven
source builds and ten minutes, live on 2026-08-26, for packages conda ships
prebuilt.

We send BARE NAMES, deliberately, and that is the whole point of this file.
An explicit per-lane spelling looks like the safer, more-informed option and is
the opposite: weft takes

    cands = [ov] if ov else lane_spellings(pkg, lane, ns)

so an override SUPPRESSES the second candidate. Naming `bioconductor-<x>` for
something that actually lives on the CRAN mirror converts a working install
into a guaranteed miss — the same failure with the sign flipped.

(An earlier version of this file asserted the opposite, having concluded weft
translated nothing. It derives; it just derived one candidate at the time.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content.bio.tools.discovery import _r_lane_request  # noqa: E402


def test_a_bioconductor_entry_is_sent_BARE():
    """The load-bearing one. weft tries r-<x> then bioconductor-<x>; an
    explicit spelling would stop it after the first."""
    assert _r_lane_request("EnsDb.Hsapiens.v86", "bioconductor") == \
        "EnsDb.Hsapiens.v86"
    assert _r_lane_request("ComplexHeatmap", "bioconductor") == \
        "ComplexHeatmap"


def test_a_cran_entry_is_sent_BARE_too():
    assert _r_lane_request("Seurat", "cran") == "Seurat"
    assert _r_lane_request("PkgX", "github") == "PkgX"


def test_we_never_hand_the_ranked_lane_an_override():
    """WIDE: whatever shape this function grows, a dict with a `conda` key
    disables weft's second candidate. If one is ever needed it must be for a
    spelling weft genuinely cannot derive, and it must arrive with its own
    reason — not as a default."""
    for src in ("bioconductor", "cran", "github", "conda"):
        e = _r_lane_request("PkgX", src)
        assert isinstance(e, str) or "conda" not in e, (src, e)


def test_a_ranked_entry_records_its_package_not_the_dict():
    """The recorder falls back to the request entry when an attempt reports no
    spelling. It must accept both shapes — weft's contract allows either, and
    a dict recorded as a package spec would send garbage to the replay."""
    from core.compute.project_env import _entry_name
    assert _entry_name({"name": "Seurat", "conda": "r-seurat"}) == "Seurat"
    assert _entry_name("Seurat") == "Seurat"


def test_the_ranked_CALL_SITE_passes_what_the_owner_returns():
    """A correct helper nobody calls is the bug unchanged."""
    src = (Path(__file__).resolve().parents[1]
           / "content" / "bio" / "tools" / "discovery.py").read_text()
    before = src[:src.index('_erk(pid, "r",')]
    assert "_r_lane_request(" in before[-500:], (
        "the entry handed to the ranked call is not built by _r_lane_request")
