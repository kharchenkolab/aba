"""Synthetic fixtures for runner tests (no corpus dependency, no biology:
a tiny build-latency investigation)."""

from __future__ import annotations

import os

from runner.events import Event, Finding, Pool, Question, Scenario

POOLS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pools")


def tiny_pool() -> Pool:
    return Pool(
        id="tiny-build-latency",
        title="CI build latency regression (synthetic)",
        domain="software performance engineering",
        questions=(
            Question("Q1", "What regressed in the CI build?"),
            Question("Q2", "What caused the cache-miss spike?"),
        ),
        findings=(
            Finding(id="G01", questions=("Q1",),
                    claim="Median CI build time regressed 9 to 14 minutes "
                          "after the runner image bump.",
                    evidence={"kind": "stat", "caption": "build times",
                              "values": {"before_min": 9, "after_min": 14}},
                    strength="strong", depends_on=(), overturns=()),
            Finding(id="G02", questions=("Q1", "Q2"),
                    claim="The regression tracks a 62% cache-miss rate on the "
                          "dependency layer, not the image bump itself.",
                    evidence={"kind": "table", "caption": "cache hits",
                              "values": {"miss_pct": 62}},
                    strength="moderate", depends_on=("G01",),
                    overturns=("G01",)),
            Finding(id="G03", questions=(),
                    claim="The artifact GC also runs 4x more often since the "
                          "quota change.",
                    evidence={"kind": "stat", "caption": "gc runs",
                              "values": {"factor": 4}},
                    strength="weak", depends_on=(), overturns=()),
        ),
    )


def scenario(events: list[dict], sid: str = "synthetic",
             pool_id: str = "tiny-build-latency") -> Scenario:
    evs = []
    for i, e in enumerate(events):
        evs.append(Event(
            index=i, t=i + 1, type=e["type"],
            anchor=e.get("anchor"), ref=e.get("ref"),
            background=bool(e.get("background", False)),
            verb=e.get("verb"), target=e.get("target"),
            text=e.get("text"), note=e.get("note"), expect=e.get("expect"),
            advance_days=int(e.get("advance_days", 0)),
        ))
    return Scenario(id=sid, pool_id=pool_id, stresses="", description="",
                    events=tuple(evs), assertions=())
