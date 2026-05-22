"""Scenario L1-DONOR — donor effect check (see eval/scenarios/scenarios.md).

Goal: does mitochondrial fraction differ by donor? Produce a clean Result.
Ground truth: D6 is the outlier; a `result` entity should exist with the figure
as evidence. Fake mode reuses the phase1 fixture (produces a figure to promote).
"""
from __future__ import annotations
import re

ID = "L1-DONOR"
GOAL = ("Check whether mitochondrial fraction differs by donor in this single-cell "
        "QC table. Make one clean figure and, if there's a clear effect, promote it "
        "to a Result stating which donor is the outlier.")
BUDGET = 8
SEED_LIVE = "eval/scenarios/data/monocyte_stim.csv"
FAKE = {"fixture": "tests/fixtures/phase1_focus.jsonl", "seed": "backend/data/cells.csv"}


def _first(view: str, prefix: str) -> str | None:
    m = re.search(rf"\[({prefix}_\w+)\]", view)
    return m.group(1) if m else None


def script(view: str, step: int, last_obs: str | None):
    if step == 0:
        return ("focus", {"entity_id": _first(view, "dat") or "workspace"})
    if step == 1:
        return ("send_message", {"text": "Plot mean mt_fraction by donor."})
    if step == 2:
        fig = _first(view, "fig")
        return ("focus", {"entity_id": fig}) if fig else ("done", {"summary": "no figure"})
    if step == 3:
        fig = _first(view, "fig")
        return ("promote_figure", {"entity_id": fig,
                "interpretation": "Donor D6 has markedly elevated mean mt_fraction "
                                  "relative to the other donors — likely low-quality."})
    return ("done", {"summary": "Recorded a result: D6 is the mt_fraction outlier."})
