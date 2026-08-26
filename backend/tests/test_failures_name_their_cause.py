"""A failure that does not name its cause costs more than no failure at all.

Live 2026-08-26. A single `ensure_capability` ran for 447 seconds and reported:

    status: partial
    note:   not ready: PKG-A(error), PKG-B(error)

The word "error". One layer down, the substrate had said exactly what happened —
a configure step could not find a system library, so the build failed and took
eight packages with it. The agent, handed six characters, told the user "env
solve error" and substituted a different library instead of the one requested.

The diagnosis was never missing. `core.compute.errors.describe` renders weft's
hints into each per-package result, and the multi-name summary threw it away
while aggregating. That is the whole defect: a reduction step that discards the
only part anyone can act on.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content.bio.mcp_servers.aba_core.tools.discovery import (  # noqa: E402
    _not_ready_note, _why_not_ready,
)


def test_the_cause_survives_the_summary():
    note = _not_ready_note([
        {"name": "PKG-A", "status": "error",
         "note": "R install failed: Cannot find xml2-config — configuration "
                 "failed for package 'XML'"},
    ])
    assert "xml2-config" in note, (
        "the summary dropped the only actionable part of the failure")
    assert "PKG-A" in note and "error" in note


def test_the_status_class_is_kept_as_well():
    """The word alone was useless; it is not useless AS A PREFIX. `not_found`
    and a failed build are different actions and must stay distinguishable at
    a glance."""
    note = _not_ready_note([{"name": "PKG-A", "status": "not_found"}])
    assert note.startswith("not ready: PKG-A: not_found")


def test_a_structured_error_payload_is_read_too():
    """DEGENERATE: some lanes return the substrate payload rather than a
    rendered note. Both shapes carry the diagnosis; both must reach the agent."""
    out = _why_not_ready({"status": "error",
                          "error": {"code": "env.realize_failed",
                                    "detail": "No such file or directory: 'g++'"}})
    assert "g++" in out and "env.realize_failed" in out


def test_a_result_with_nothing_to_say_degrades_to_its_status():
    """WIDE: never raise and never invent. A result that genuinely carries no
    reason must still render — the old behaviour is the floor, not the norm."""
    assert _why_not_ready({"name": "PKG-A", "status": "candidates"}) == "candidates"
    assert _why_not_ready({}) == "?"


def test_several_packages_each_name_their_own_cause():
    """The failure that prompted this had TWO packages and one shared cause;
    a summary that collapses them loses which is which."""
    note = _not_ready_note([
        {"name": "PKG-A", "status": "error", "note": "reason one"},
        {"name": "PKG-B", "status": "not_found", "note": "reason two"},
    ])
    assert "PKG-A: error — reason one" in note
    assert "PKG-B: not_found — reason two" in note
    assert note.count(";") == 1, "one separator per extra package"


def test_a_long_cause_is_bounded_but_not_erased():
    long = "x" * 5000
    out = _why_not_ready({"status": "error", "note": long})
    assert 30 < len(out) < 400, len(out)
    assert "x" in out
