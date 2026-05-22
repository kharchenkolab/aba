"""Scenario L1-QC — QC triage (see eval/scenarios/scenarios.md).

Goal: is the data usable, and is any sample bad? Ground truth: donor D6 has high
mt_fraction; agent should identify it and keep a QC figure.

Two ways to run:
  - live  : LLMPolicy (Haiku) drives toward GOAL on the real dataset.
  - fake  : a scripted FnPolicy + the phase1 fake-Guide fixture — deterministic,
            zero-token plumbing check (the fixture plots mt_fraction on cells.csv).
"""
from __future__ import annotations
import re

ID = "L1-QC"
GOAL = ("I just received this single-cell RNA-seq table (per-cell QC metrics by "
        "donor and condition). Determine whether the data is usable and whether "
        "any donor/sample looks low-quality. Keep the figure that shows the "
        "problem. Conclude which sample, if any, is bad.")
BUDGET = 8
SEED_LIVE = "eval/scenarios/data/monocyte_stim.csv"

# Fake-mode plumbing check: the phase1 fixture replays a Guide turn that plots
# mt_fraction from backend/data/cells.csv, so the scripted scientist below can
# drive the full loop with zero tokens.
FAKE = {
    "fixture": "tests/fixtures/phase1_focus.jsonl",
    "seed": "backend/data/cells.csv",
}


def _first(view: str, prefix: str) -> str | None:
    m = re.search(rf"\[({prefix}_\w+)\]", view)
    return m.group(1) if m else None


def script(view: str, step: int, last_obs: str | None):
    """Deterministic scientist for fake-mode validation."""
    if step == 0:
        ds = _first(view, "dat")
        return ("focus", {"entity_id": ds or "workspace"})
    if step == 1:
        return ("send_message", {"text": "Plot the mt_fraction distribution by "
                                          "donor so I can spot any low-quality samples."})
    if step == 2:
        fig = _first(view, "fig")
        return ("focus", {"entity_id": fig}) if fig else ("done", {"summary": "no figure produced"})
    if step == 3:
        fig = _first(view, "fig")
        return ("pin", {"entity_id": fig}) if fig else ("done", {"summary": "no figure to keep"})
    return ("done", {"summary": "Donor D6 looks like the QC outlier (elevated "
                                "mt_fraction); kept the QC figure."})
