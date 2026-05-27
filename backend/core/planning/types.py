"""Structured-plan dataclasses (T2.5).

A `present_plan` tool call is normalized into a Plan object; the
validator runs against this shape and emits concerns the UI shows
inline. Pre-execution advisor review hooks here in a future iteration.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    n: int
    title: str
    description: str = ""
    expected_outputs: list[str] = field(default_factory=list)
    skill: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n": self.n, "title": self.title, "description": self.description,
            "expected_outputs": list(self.expected_outputs),
            "skill": self.skill, "parameters": dict(self.parameters),
        }


@dataclass
class PlanConcern:
    """One validator finding. `level` is 'info' | 'warn' | 'error'; the UI
    surfaces info/warn inline and lets the user proceed."""
    step_n: int | None             # None for plan-level concerns
    level: str
    message: str

    def to_dict(self) -> dict:
        return {"step_n": self.step_n, "level": self.level, "message": self.message}


@dataclass
class Plan:
    title: str = ""
    summary: str = ""
    rationale: str = ""
    assumptions: list[str] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)
    concerns: list[PlanConcern] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "rationale": self.rationale,
            "assumptions": list(self.assumptions),
            "steps": [s.to_dict() for s in self.steps],
            "concerns": [c.to_dict() for c in self.concerns],
        }
