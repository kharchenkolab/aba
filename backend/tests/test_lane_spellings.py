"""The conda lane cannot resolve an R library name.

conda-forge and bioconda spell R packages `r-<lowercase>` and
`bioconductor-<lowercase>`; the R world spells them by their library name.
weft's ranked mode takes `{"name": ..., "<lane>": "<spelling>"}` and translates
nothing itself — correctly, it cannot know our vocabulary.

aba passed BARE names. So with `lanes=["conda", "cran"]` the conda lane was
asked for a conda package called `EnsDb.Hsapiens.v86`, which cannot exist. It
missed every time, and every R package fell through to the cran lane — which
builds from SOURCE.

Live 2026-08-26: eleven `installing *source* package`, ten minutes, then a
build failure — for packages conda ships prebuilt. The lane ORDER was right the
whole time (conda first); the vocabulary was wrong.

The translation already existed in the legacy cascade the ranked path replaced.
The rewrite dropped it. These guards pin that both call one owner.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content.bio.tools.discovery import (  # noqa: E402
    _r_conda_spelling, _r_lane_request,
)


def test_a_bioconductor_package_gets_its_conda_spelling():
    assert _r_conda_spelling("EnsDb.Hsapiens.v86", "bioconductor") == \
        "bioconductor-ensdb.hsapiens.v86"
    assert _r_conda_spelling("biovizBase", "bioconductor") == \
        "bioconductor-biovizbase"


def test_a_cran_package_gets_its_conda_spelling():
    assert _r_conda_spelling("ComplexHeatmap", "cran") == "r-complexheatmap"
    assert _r_conda_spelling("Seurat", "cran") == "r-seurat"


def test_an_already_conda_spelled_name_is_left_alone():
    """DEGENERATE: the caller may already have handed us the conda name."""
    assert _r_conda_spelling("r-seurat", "cran") == "r-seurat"
    assert _r_conda_spelling("bioconductor-deseq2", "bioconductor") == \
        "bioconductor-deseq2"


def test_a_bioconductor_entry_carries_the_spelling_weft_cannot_derive():
    """The load-bearing one. weft derives `r-<lowercase>` for a bare R name on
    the conda lane — right for CRAN, wrong for Bioconductor, and nothing in a
    bare name says which repository it came from. Only the caller knows."""
    e = _r_lane_request("EnsDb.Hsapiens.v86", "bioconductor")
    assert e["name"] == "EnsDb.Hsapiens.v86"
    assert e["conda"] == "bioconductor-ensdb.hsapiens.v86"
    assert e["cran"] == "EnsDb.Hsapiens.v86", (
        "the cran lane must still get the R name — translating BOTH would "
        "just move the miss to the other lane")


def test_a_cran_entry_stays_bare_so_weft_owns_the_derivation():
    """WIDE, and deliberate: `lane_spelling` is weft's ONE derivation, used by
    the chain AND the probe. Re-implementing it here for the case it already
    gets right would be the split-brain its docstring warns about."""
    assert _r_lane_request("Seurat", "cran") == "Seurat"
    assert _r_lane_request("PkgX", "github") == "PkgX"


def test_the_entry_shape_is_the_one_weft_accepts():
    """weft refuses anything but a string or {name, conda|pypi|cran}. An extra
    key is a refusal at intake, i.e. no install at all."""
    e = _r_lane_request("DESeq2", "bioconductor")
    assert set(e) <= {"name", "conda", "pypi", "cran"}, set(e)


def test_a_ranked_entry_records_its_package_not_the_dict():
    """The recorder falls back to the request entry when an attempt reports no
    spelling. With dict entries that fallback would write a dict into the
    session's addition specs, and the replay would send garbage to weft."""
    from core.compute.project_env import _entry_name
    assert _entry_name({"name": "Seurat", "conda": "r-seurat"}) == "Seurat"
    assert _entry_name("Seurat") == "Seurat"


def test_the_ranked_CALL_SITE_uses_the_owner():
    """A correct helper nobody calls is the bug unchanged.

    The defect was never in the translation rule — that rule sat twelve lines
    below, in the legacy cascade, working. It was that the ranked path did not
    USE it. So the guard has to be on the call, not only on the function.
    """
    src = (Path(__file__).resolve().parents[1]
           / "content" / "bio" / "tools" / "discovery.py").read_text()
    call = src[src.index('_erk(pid, "r",'):]
    call = call[:call.index(")")]
    assert "_entry" in call or "_r_lane_request" in call, (
        f"the ranked R call passes a bare name again: {call!r}")
    # and the entry it passes must come from the owner
    before = src[:src.index('_erk(pid, "r",')]
    assert "_r_lane_request(" in before[-500:], (
        "the entry handed to the ranked call is not built by _r_lane_request")


def test_both_paths_share_one_translation_owner():
    """The legacy cascade had its own copy of the rule. Two copies is how they
    drift — and how one of them silently stops being used."""
    src = (Path(__file__).resolve().parents[1]
           / "content" / "bio" / "tools" / "discovery.py").read_text()
    inline = src.count('f"bioconductor-{')
    assert inline == 1, (
        f"the bioconductor spelling rule appears {inline} times; it belongs "
        f"only inside _r_conda_spelling")
