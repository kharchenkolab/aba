"""Event, pool and scenario loading for the Record eval runner.

Parses `pool.json` and scenario JSON files (HANDOFF.md section 3 and section 7,
event schema v1.1) into typed, immutable objects, validating every reference:
finding refs against the pool, gesture targets against findings, session
anchors against questions.  Assertions are carried verbatim (they are compiled
in S3, not here).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

EVENT_TYPES = {
    "session_start",
    "finding",
    "gesture",
    "instruction",
    "ratify",
    "dismiss",
    "distill",
    "clock",
}

GESTURE_VERBS = {
    "pin", "expand", "check", "fade", "corroborate",
    "alternatives", "plan", "draft_claim", "hold",
}
#: curation family acts immediately on the representation space
CURATION_VERBS = {"pin", "fade", "hold", "draft_claim"}
#: investigation family compiles typed plan items; scrutiny verbs additionally
#: mark their target provisional (RECORD_DESIGN section 6)
INVESTIGATION_VERBS = {"check", "corroborate", "alternatives", "expand", "plan"}
SCRUTINY_VERBS = {"check", "corroborate", "alternatives"}

#: event types that constitute scientist attention (used by the absence /
#: timer-freeze rule in engine.py).  A foreground `finding` event is scientist
#: -driven (it lands inside a sitting the scientist is attending); a
#: background finding is machine work and does NOT count as attention.
SCIENTIST_EVENT_TYPES = {
    "session_start", "gesture", "instruction", "ratify", "dismiss", "distill",
}

STRENGTHS = ("weak", "moderate", "strong")


class CorpusError(ValueError):
    """A pool or scenario file failed reference validation."""


@dataclass(frozen=True)
class Question:
    id: str
    text: str


@dataclass(frozen=True)
class Finding:
    id: str
    questions: tuple[str, ...]
    claim: str
    evidence: dict
    strength: str
    depends_on: tuple[str, ...]
    overturns: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class Pool:
    id: str
    title: str
    domain: str
    questions: tuple[Question, ...]
    findings: tuple[Finding, ...]

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(q.id for q in self.questions)

    def finding(self, fid: str) -> Finding:
        return self._by_id()[fid]

    def has_finding(self, fid: str) -> bool:
        return fid in self._by_id()

    def _by_id(self) -> dict[str, Finding]:
        cache = object.__getattribute__(self, "__dict__").get("_cache")
        if cache is None:
            cache = {f.id: f for f in self.findings}
            object.__getattribute__(self, "__dict__")["_cache"] = cache
        return cache


@dataclass(frozen=True)
class Event:
    """One scenario event.  Unused fields are None."""

    index: int              # 0-based position in the stream
    t: int                  # authored `t` value (1-based, may skip)
    type: str
    anchor: str | None = None        # session_start
    ref: str | None = None           # finding
    background: bool = False         # finding
    verb: str | None = None          # gesture
    target: str | None = None        # gesture / ratify / dismiss
    text: str | None = None          # instruction
    note: str | None = None          # gesture / ratify / dismiss
    expect: str | None = None        # instruction / ratify / dismiss (authoring
                                     # guidance -- honest policies must not read it)
    advance_days: int = 0            # clock


@dataclass(frozen=True)
class Scenario:
    id: str
    pool_id: str
    stresses: str
    description: str
    events: tuple[Event, ...]
    assertions: tuple[dict, ...] = field(default=())


def load_pool(pool_dir: str) -> Pool:
    path = os.path.join(pool_dir, "pool.json")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    questions = tuple(Question(id=q["id"], text=q["text"]) for q in raw["questions"])
    findings = []
    for f in raw["findings"]:
        strength = f["strength"]
        if strength not in STRENGTHS:
            raise CorpusError(f"{path}: finding {f['id']} bad strength {strength!r}")
        findings.append(Finding(
            id=f["id"],
            questions=tuple(f.get("questions", ())),
            claim=f["claim"],
            evidence=dict(f.get("evidence", {})),
            strength=strength,
            depends_on=tuple(f.get("depends_on", ())),
            overturns=tuple(f.get("overturns", ())),
            notes=f.get("notes", ""),
        ))
    pool = Pool(
        id=raw["id"],
        title=raw.get("title", raw["id"]),
        domain=raw.get("domain", ""),
        questions=questions,
        findings=tuple(findings),
    )
    _validate_pool(pool, path)
    return pool


def _validate_pool(pool: Pool, path: str) -> None:
    qids = set(pool.question_ids)
    fids = {f.id for f in pool.findings}
    problems = []
    for f in pool.findings:
        for q in f.questions:
            if q not in qids:
                problems.append(f"finding {f.id}: unknown question {q}")
        for d in f.depends_on:
            if d not in fids:
                problems.append(f"finding {f.id}: unknown depends_on {d}")
        for o in f.overturns:
            if o not in fids:
                problems.append(f"finding {f.id}: unknown overturns {o}")
    if problems:
        raise CorpusError(f"{path}: " + "; ".join(problems))


def load_scenario(path: str, pool: Pool) -> Scenario:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    events = []
    problems = []
    landed: set[str] = set()
    for i, e in enumerate(raw["events"]):
        etype = e["type"]
        if etype not in EVENT_TYPES:
            problems.append(f"event t={e.get('t')}: unknown type {etype!r}")
            continue
        ev = Event(
            index=i,
            t=int(e["t"]),
            type=etype,
            anchor=e.get("anchor"),
            ref=e.get("ref"),
            background=bool(e.get("background", False)),
            verb=e.get("verb"),
            target=e.get("target"),
            text=e.get("text"),
            note=e.get("note"),
            expect=e.get("expect"),
            advance_days=int(e.get("advance_days", 0)),
        )
        if etype == "session_start":
            if ev.anchor not in set(pool.question_ids):
                problems.append(f"event t={ev.t}: unknown anchor {ev.anchor!r}")
        elif etype == "finding":
            if not pool.has_finding(ev.ref or ""):
                problems.append(f"event t={ev.t}: unknown finding ref {ev.ref!r}")
            else:
                missing = [d for d in pool.finding(ev.ref).depends_on
                           if d not in landed]
                if missing:
                    problems.append(
                        f"event t={ev.t}: {ev.ref} lands before its "
                        f"dependencies {missing}")
                landed.add(ev.ref)
        elif etype == "gesture":
            if ev.verb not in GESTURE_VERBS:
                problems.append(f"event t={ev.t}: unknown verb {ev.verb!r}")
            if not pool.has_finding(ev.target or ""):
                problems.append(
                    f"event t={ev.t}: gesture target {ev.target!r} is not a "
                    f"pool finding")
        elif etype in ("ratify", "dismiss"):
            if not ev.target:
                problems.append(f"event t={ev.t}: {etype} without target")
        elif etype == "clock":
            if ev.advance_days <= 0:
                problems.append(f"event t={ev.t}: clock without advance_days")
        events.append(ev)
    if problems:
        raise CorpusError(f"{path}: " + "; ".join(problems))
    return Scenario(
        id=raw["id"],
        pool_id=raw["pool"],
        stresses=raw.get("stresses", ""),
        description=raw.get("description", ""),
        events=tuple(events),
        assertions=tuple(raw.get("assertions", ())),
    )


def load_all_scenarios(pool_dir: str, pool: Pool) -> list[Scenario]:
    sdir = os.path.join(pool_dir, "scenarios")
    out = []
    for name in sorted(os.listdir(sdir)):
        if name.endswith(".json"):
            out.append(load_scenario(os.path.join(sdir, name), pool))
    return out
