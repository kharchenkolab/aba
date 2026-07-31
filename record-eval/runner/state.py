"""The Record state model — a minimal but real Record.

Holds every store the semantics rules need: the outline tree of
question-owned sections, prose blocks with maturity/provenance/flags, three
strata, routing rows and per-distill routing tables, the classed consent
queue with Class-2 active-day expiry, plan items with lifecycle + provisional
marks, salience marks, sittings/episodes, and superseded-but-findable history
(revisions append; nothing is ever deleted).

Time: `day` advances only through scenario clock events (REPORT section 6.6 —
finding-internal dates are opaque content).  Governance-timer accrual is the
ENGINE's job (see engine.py for the documented absence rule); the state only
stores `active_age` per proposal.

Question roots: at init the state creates one `question-root` node per pool
question.  Roots are furniture, not policy structure — a policy may write
question-keyed prose into a root without ever emitting a structural op
(this is what makes the `never_restructure` baseline expressible).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .events import Pool

CLASS2_EXPIRY_DAYS = 14   # "~14 days" of ACTIVE time (section 14.1); frozen in absence


@dataclass
class Section:
    id: str
    question_id: str
    title: str
    kind: str = "stub"              # 'question-root' | 'stub' | 'section'
    parent_id: str | None = None
    maturity: str = "conjecture"
    rank: str = "main"              # 'main' | 'appendix'
    status: str = "live"            # 'live' | 'closed' | 'merged'
    intent: bool = False
    charge: str = ""
    created_day: int = 0
    created_event: int = -1
    merged_into: str | None = None
    cited: list[str] = field(default_factory=list)   # finding ids (story cites)
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "question_id": self.question_id, "title": self.title,
            "kind": self.kind, "parent_id": self.parent_id,
            "maturity": self.maturity, "rank": self.rank, "status": self.status,
            "intent": self.intent, "charge": self.charge,
            "created_day": self.created_day, "created_event": self.created_event,
            "merged_into": self.merged_into, "cited": list(self.cited),
            "history": list(self.history),
        }


@dataclass
class ProseBlock:
    id: str
    section_id: str
    text: str
    maturity: str = "conjecture"
    provenance: list[str] = field(default_factory=list)
    authored: bool = False
    ratified: bool = False
    contested: bool = False         # a Class-X addendum proposal targets it
    superseded_by: str | None = None    # revision chain: never deleted
    revision_of: str | None = None
    addenda: list[str] = field(default_factory=list)
    created_day: int = 0
    created_event: int = -1

    def to_dict(self) -> dict:
        return {
            "id": self.id, "section_id": self.section_id, "text": self.text,
            "maturity": self.maturity, "provenance": list(self.provenance),
            "authored": self.authored, "ratified": self.ratified,
            "contested": self.contested, "superseded_by": self.superseded_by,
            "revision_of": self.revision_of, "addenda": list(self.addenda),
            "created_day": self.created_day, "created_event": self.created_event,
        }


@dataclass
class Addendum:
    id: str
    prose_id: str
    text: str
    provenance: list[str] = field(default_factory=list)
    day: int = 0                    # the DATED part of the addendum grammar
    event: int = -1

    def to_dict(self) -> dict:
        return {"id": self.id, "prose_id": self.prose_id, "text": self.text,
                "provenance": list(self.provenance), "day": self.day,
                "event": self.event}


@dataclass
class Proposal:
    id: str
    cls: str                        # '0'..'3' | 'X'
    kind: str                       # 'claim_draft' | 'addendum' | 'restructuring' | ...
    description: str
    payload: list = field(default_factory=list)     # list[Op]
    status: str = "pending"         # pending|accepted|expired|dismissed|withdrawn
    applied: bool = False
    active_age: int = 0             # ACTIVE days accrued (engine's absence rule)
    created_day: int = 0
    created_event: int = -1
    decided_day: int | None = None
    decided_event: int | None = None
    withdraw_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "cls": self.cls, "kind": self.kind,
            "description": self.description,
            "payload": [op.to_dict() for op in self.payload],
            "status": self.status, "applied": self.applied,
            "active_age": self.active_age,
            "created_day": self.created_day, "created_event": self.created_event,
            "decided_day": self.decided_day, "decided_event": self.decided_event,
            "withdraw_reason": self.withdraw_reason,
        }


@dataclass
class PlanItem:
    id: str
    owner: str                      # question id (plan lives in the section it feeds)
    kind: str                       # check|corroborate|alternatives|expand|plan|analysis
    text: str = ""
    target: str | None = None       # finding under scrutiny
    state: str = "planned"          # proposed|planned|taken-up|produced|absorbed
    provisional: bool = False       # True while this scrutiny mark holds its target
    origin: str = "policy"          # 'gesture' | 'policy'
    created_day: int = 0
    created_event: int = -1
    discharged_by: str | None = None    # routing-row id that closed it
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "owner": self.owner, "kind": self.kind,
            "text": self.text, "target": self.target, "state": self.state,
            "provisional": self.provisional, "origin": self.origin,
            "created_day": self.created_day, "created_event": self.created_event,
            "discharged_by": self.discharged_by, "history": list(self.history),
        }


@dataclass
class RoutingRow:
    id: str
    product: str                    # finding id (or draft id)
    stratum: str                    # story|notes|sediment
    questions: list[str] = field(default_factory=list)
    note: str = ""
    discharges: list[str] = field(default_factory=list)
    typed: str = "routine"          # routine rows settle at distill; decisions
                                    # live in the consent queue instead
    status: str = "pending"         # pending|accepted|faded
    origin: str = "policy"
    created_day: int = 0
    created_event: int = -1
    settled_event: int | None = None
    sitting_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "product": self.product, "stratum": self.stratum,
            "questions": list(self.questions), "note": self.note,
            "discharges": list(self.discharges), "typed": self.typed,
            "status": self.status, "origin": self.origin,
            "created_day": self.created_day, "created_event": self.created_event,
            "settled_event": self.settled_event, "sitting_id": self.sitting_id,
        }


@dataclass
class FindingRecord:
    id: str
    landed_day: int
    landed_event: int
    background: bool = False
    sitting_id: str | None = None
    pinned: bool = False
    faded: bool = False
    superseded_by: str | None = None    # face-value reading superseded; findable
    superseded_note: str = ""
    provisional_open: list[str] = field(default_factory=list)  # plan-item ids
    citations: list[dict] = field(default_factory=list)
    # each: {stratum, question, row, day, revised: bool, note}

    def to_dict(self) -> dict:
        return {
            "id": self.id, "landed_day": self.landed_day,
            "landed_event": self.landed_event, "background": self.background,
            "sitting_id": self.sitting_id, "pinned": self.pinned,
            "faded": self.faded, "superseded_by": self.superseded_by,
            "superseded_note": self.superseded_note,
            "provisional_open": list(self.provisional_open),
            "citations": list(self.citations),
        }


@dataclass
class Sitting:
    id: str
    episode_id: str
    anchor: str
    opened_day: int
    opened_event: int
    state: str = "open"             # open|parked|filed
    findings: list[str] = field(default_factory=list)
    gestures: list[dict] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)     # question ids
    silent_filed: bool = False
    leftovers: list[str] = field(default_factory=list)
    closed_day: int | None = None
    closed_event: int | None = None

    def residue(self) -> bool:
        return bool(self.findings or self.gestures)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "episode_id": self.episode_id, "anchor": self.anchor,
            "opened_day": self.opened_day, "opened_event": self.opened_event,
            "state": self.state, "findings": list(self.findings),
            "gestures": list(self.gestures), "touched": sorted(set(self.touched)),
            "silent_filed": self.silent_filed, "leftovers": list(self.leftovers),
            "closed_day": self.closed_day, "closed_event": self.closed_event,
        }


class RecordState:
    """All stores plus deterministic id allocation.  Mutated only by the engine."""

    def __init__(self, pool: Pool):
        self.pool = pool
        self.day = 0
        self.event_index = -1
        self._counters: dict[str, int] = {}

        self.sections: dict[str, Section] = {}
        self.prose: dict[str, ProseBlock] = {}
        self.addenda: dict[str, Addendum] = {}
        self.proposals: dict[str, Proposal] = {}
        self.plan_items: dict[str, PlanItem] = {}
        self.routing_rows: dict[str, RoutingRow] = {}
        self.routing_tables: list[dict] = []
        self.findings: dict[str, FindingRecord] = {}
        self.sittings: dict[str, Sitting] = {}
        self.episodes: dict[tuple[str, int], str] = {}
        self.live_sitting_id: str | None = None
        self.salience: dict[str, str] = {}     # target -> pinned|faded
        self.holds: list[str] = []             # evaporate at sitting end
        self.briefings: list[dict] = []
        self.absences: list[dict] = []         # frozen gaps, for the honest record
        self.pending_gap_days = 0              # clock days since last scientist event

        self.question_roots: dict[str, str] = {}
        for q in pool.questions:
            sid = self.new_id("S")
            self.sections[sid] = Section(
                id=sid, question_id=q.id, title=q.text, kind="question-root")
            self.question_roots[q.id] = sid

    # -- ids ----------------------------------------------------------------

    def new_id(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}{n}"

    # -- section resolution --------------------------------------------------

    def resolve_section(self, ref: str) -> Section | None:
        """Resolve a section ref: raw id, 'Q1' (root) or 'Q1/Title'."""
        if ref in self.sections:
            return self.sections[ref]
        if ref in self.question_roots:
            return self.sections[self.question_roots[ref]]
        if "/" in ref:
            qid, _, title = ref.partition("/")
            for s in self.sections.values():
                if s.question_id == qid and s.title == title and s.status != "merged":
                    return s
        return None

    def sections_for_question(self, qid: str) -> list[Section]:
        return [s for s in self.sections.values() if s.question_id == qid]

    # -- export (deterministic) ----------------------------------------------

    def export(self) -> dict:
        return {
            "day": self.day,
            "event_index": self.event_index,
            "sections": {k: v.to_dict() for k, v in sorted(self.sections.items())},
            "prose": {k: v.to_dict() for k, v in sorted(self.prose.items())},
            "addenda": {k: v.to_dict() for k, v in sorted(self.addenda.items())},
            "proposals": {k: v.to_dict() for k, v in sorted(self.proposals.items())},
            "plan_items": {k: v.to_dict() for k, v in sorted(self.plan_items.items())},
            "routing_rows": {k: v.to_dict() for k, v in sorted(self.routing_rows.items())},
            "routing_tables": list(self.routing_tables),
            "findings": {k: v.to_dict() for k, v in sorted(self.findings.items())},
            "sittings": {k: v.to_dict() for k, v in sorted(self.sittings.items())},
            "live_sitting": self.live_sitting_id,
            "salience": dict(sorted(self.salience.items())),
            "holds": sorted(self.holds),
            "briefings": list(self.briefings),
            "absences": list(self.absences),
        }


class RecordView:
    """Read-only face of the state handed to policies.

    Query methods return copies / plain data.  Policies MUST NOT mutate the
    record; all writes go through ops.
    """

    def __init__(self, state: RecordState):
        self._s = state

    # time / attention
    @property
    def day(self) -> int:
        return self._s.day

    @property
    def live_sitting(self) -> dict | None:
        sid = self._s.live_sitting_id
        return self._s.sittings[sid].to_dict() if sid else None

    @property
    def anchor(self) -> str | None:
        sid = self._s.live_sitting_id
        return self._s.sittings[sid].anchor if sid else None

    # structure
    def question_ids(self) -> list[str]:
        return list(self._s.pool.question_ids)

    def question_root(self, qid: str) -> str | None:
        return self._s.question_roots.get(qid)

    def section(self, ref: str) -> dict | None:
        s = self._s.resolve_section(ref)
        return s.to_dict() if s else None

    def sections(self, question_id: str | None = None) -> list[dict]:
        out = [s for s in self._s.sections.values()
               if question_id is None or s.question_id == question_id]
        return [s.to_dict() for s in sorted(out, key=lambda s: s.id)]

    def has_section(self, qid: str, title: str) -> bool:
        return self._s.resolve_section(f"{qid}/{title}") is not None

    # prose
    def prose_blocks(self, citing: str | None = None,
                     ratified: bool | None = None,
                     section: str | None = None) -> list[dict]:
        sec = self._s.resolve_section(section) if section else None
        out = []
        for p in sorted(self._s.prose.values(), key=lambda p: p.id):
            if citing is not None and citing not in p.provenance:
                continue
            if ratified is not None and p.ratified != ratified:
                continue
            if sec is not None and p.section_id != sec.id:
                continue
            out.append(p.to_dict())
        return out

    def has_committed_prose_citing(self, fid: str) -> bool:
        """Is this finding's reading carried by any live (non-superseded) prose?"""
        return any(p.superseded_by is None and fid in p.provenance
                   for p in self._s.prose.values())

    def has_ratified_prose_citing(self, fid: str) -> bool:
        return any(p.superseded_by is None and fid in p.provenance
                   and (p.ratified or p.authored)
                   for p in self._s.prose.values())

    # consent queue
    def pending_proposals(self, kind: str | None = None,
                          cls: str | None = None) -> list[dict]:
        out = [p for p in self._s.proposals.values() if p.status == "pending"
               and (kind is None or p.kind == kind)
               and (cls is None or p.cls == cls)]
        return [p.to_dict() for p in sorted(out, key=lambda p: p.created_event)]

    def proposal(self, pid: str) -> dict | None:
        p = self._s.proposals.get(pid)
        return p.to_dict() if p else None

    # plan
    def plan_items(self, owner: str | None = None, kind: str | None = None,
                   target: str | None = None, state: str | None = None) -> list[dict]:
        out = [i for i in self._s.plan_items.values()
               if (owner is None or i.owner == owner)
               and (kind is None or i.kind == kind)
               and (target is None or i.target == target)
               and (state is None or i.state == state)]
        return [i.to_dict() for i in sorted(out, key=lambda i: i.created_event)]

    # findings / routing / salience
    def finding_record(self, fid: str) -> dict | None:
        r = self._s.findings.get(fid)
        return r.to_dict() if r else None

    def pending_rows(self, product: str | None = None) -> list[dict]:
        out = [r for r in self._s.routing_rows.values() if r.status == "pending"
               and (product is None or r.product == product)]
        return [r.to_dict() for r in sorted(out, key=lambda r: r.created_event)]

    def salience(self, target: str) -> str | None:
        return self._s.salience.get(target)
