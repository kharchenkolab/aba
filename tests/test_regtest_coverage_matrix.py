"""regtest sweep — the coverage MATRIX, not the scenario count.

Two bugs reached production in 2026-08 and the escape analysis for both ended
in the same place: the suite had 48 scenarios and a hole shaped exactly like
the bug. Neither hole was visible from a list of scenarios; both were obvious
the moment the scenarios were cross-tabulated.

  * R x BACKGROUND was EMPTY. Three scenarios assert on a background job and
    none of them mentions R; two put run_r in tools_used and neither uses
    background. Every background scenario was Python and every R scenario was
    foreground, so the background-R lane shipped with no agent-level coverage
    — and that is precisely where "background R jobs fail instantly" landed.

  * Every R package the suite named lived on conda-forge (RNetCDF, Seurat,
    jsonlite, praise). Isolated-env specs carried no channel list, so they
    solved against conda-forge alone — which no scenario could detect, because
    a conda-forge-resident package solves fine either way. The packages that
    broke in the field (r-signac, the bioconductor-* set) are bioconda-only.

A scenario suite is a matrix, and a cell nobody is watching empties silently
as scenarios are added, retired and rewritten. These assertions watch two
cells whose emptiness has already cost us a production incident each.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

SCENARIOS = Path(__file__).resolve().parents[1] / "regtest" / "scenarios"


def _docs():
    yaml = pytest.importorskip("yaml")
    for p in sorted(SCENARIOS.glob("*/scenario.yaml")):
        try:
            doc = yaml.safe_load(p.read_text()) or {}
        except Exception:                     # a malformed scenario is its own test
            continue
        yield p.parent.name, doc, p.read_text()


def _tools(doc):
    out = set()
    for step in doc.get("steps") or []:
        for t in ((step.get("expect") or {}).get("tools_used") or []):
            out.add(str(t))
    return out


def _asserts_background(doc):
    return any((step.get("expect") or {}).get("background_job")
               for step in (doc.get("steps") or []))


def test_r_background_cell_is_not_empty():
    """The cell bug #1 lived in. An R scenario that asserts on a real
    background job — foreground R proves nothing about this lane, because the
    two resolve their environment by different routes (live session vs. the
    session's frozen snapshot), which is why the failure was invisible until a
    user backgrounded something."""
    both = [name for name, doc, _ in _docs()
            if "run_r" in _tools(doc) and _asserts_background(doc)]
    assert both, (
        "no scenario exercises R in a BACKGROUND job: every background "
        "scenario is python and every R scenario is foreground. That empty "
        "cell is where the 2026-08 background-R failure shipped from.")


def test_some_scenario_installs_a_bioconda_only_package():
    """The cell bug #2 lived in. `bioconductor-*` is the mechanically
    identifiable bioconda-only family (it is why weft's own channel hint keys
    on that prefix), so requiring one install target from it keeps the suite
    honest about channels. A suite whose R packages all happen to live on
    conda-forge cannot tell a correct channel list from a missing one."""
    named = [name for name, _doc, text in _docs() if "bioconductor-" in text]
    assert named, (
        "no scenario installs a bioconductor-* package, so nothing exercises "
        "the bioconda channel: an isolated-env spec that lists no channels "
        "solves conda-forge-only and every scenario still passes.")
