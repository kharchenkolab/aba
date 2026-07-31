"""The replay engine and THE GATE.

The engine folds scenario events over the Record state, calling the policy at
each editorial moment and applying returned ops through one gate.

THE GATE (non-negotiable, enforced in transition code as asserts):

1. **Consent conservation** — no op whose EFFECTIVE class is '2', '3' or 'X'
   applies unless it arrives as the payload of a proposal that is `accepted`
   (via an explicit ratify).  The effective class is max(declared class,
   per-op-type floor) — see ops.class_floor: a policy declaring
   `DemoteSection(cls="1")` is treated at the floor ('3'), so misdeclaration
   cannot buy a cheaper consent tier.  MarkSuperseded carries a
   state-dependent floor: 'X' while live ratified/authored prose cites the
   finding (invalidating ratified prose is the interrupt, not a Class-1
   touch-up).  Class-2 proposals expire (lapse) when their active-day timer
   runs out — an expired or pending or dismissed proposal can never apply.
   Writing prose flagged `ratified`/`authored` floors at the decision class
   ('only the user writes').

   *Consent ceiling*: a proposal's class must dominate every payload op's
   effective class — asserted at propose time AND re-asserted per payload op
   at apply time (the state may have changed in between, e.g. prose ratified
   after the propose).  A Class-2 wrapper can therefore never smuggle a
   Class-3/X payload through a cheap consent.  Note the residual, structural
   gap: ratify matches proposals by free-text DESCRIPTION, and no gate can
   verify that a description honestly summarizes its payload — the class
   ceiling is the enforceable part; description honesty is graded (S3), not
   gated.

2. **Authored-text immutability** — prose flagged ratified or authored is
   never rewritten by any op (ReviseProse on it violates, even inside a
   consented payload); revision arrives only as a dated, provenance-linked
   AddAddendum applied through an accepted proposal.

Both raise :class:`GateViolation` (an AssertionError subclass).

Section 14.2 hysteresis (N-consecutive-cycles) is delegated to policies —
the engine models no waiting period, so the decisive-evidence rule is
enforced only on its consent-class half (a same-cycle proposal is legal;
applying it still takes the classed consent).

ABSENCE / TIMER-FREEZE RULE (RECORD_DESIGN section 14.7), exact form:

    Scenario time advances only through `clock` events.  Consecutive clock
    events with no scientist-driven event between them (session_start,
    gesture, instruction, ratify, dismiss, distill, or a FOREGROUND finding;
    background findings are machine work and do not count) form one
    *attention gap* whose length is the sum of their `advance_days`.  When
    scientist attention returns (or the scenario ends), the gap is settled:

      * gap <= ABSENCE_GAP_DAYS (3): normal working cadence — the scientist
        is around, merely not acting on the tray — the FULL gap accrues to
        every pending Class-2 proposal's `active_age`; any proposal reaching
        CLASS2_EXPIRY_DAYS (14) lapses visibly at that boundary (status
        'expired', never silently applied).
      * gap >  ABSENCE_GAP_DAYS: an absence — the fast clock ran (background
        findings landed in sediment, day advanced) but governance timers
        FROZE: the gap contributes ZERO active days, and the gap is recorded
        in `state.absences` so the record shows it honestly.

    The 3-day threshold reads section 14.7's "past a few days away" as: gaps
    of up to three days are the ordinary rhythm of a working week; anything
    longer is time away.  Expiry therefore consumes only days adjacent to
    actual scientist activity, and a 21- or 24-day gap leaves a ~14-day
    Class-2 timer exactly where it stood.

Session semantics: sessions end by attention — the next `session_start`
parks the live sitting, a `clock` files it (and the scenario end files it).
Hold marks evaporate whenever the live sitting ends.  Same-anchor, same-day
micro-bursts coalesce into one episode.  A sitting that leaves no residue
files silently.  Foreground findings land in the sediment at launch, marked
with the sitting that produced them; `background: true` findings (or
findings with no live sitting) land in the sediment with no sitting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import ops as O
from .events import (Event, Pool, Scenario, SCIENTIST_EVENT_TYPES,
                     SCRUTINY_VERBS, INVESTIGATION_VERBS)
from .policy import Moment, Policy
from .state import (CLASS2_EXPIRY_DAYS, Addendum, FindingRecord, PlanItem,
                    Proposal, ProseBlock, RecordState, RecordView, RoutingRow,
                    Section, Sitting)
from .trace import Trace

ABSENCE_GAP_DAYS = 3


class GateViolation(AssertionError):
    """One of the two hard invariants was violated by an op."""


class EngineError(RuntimeError):
    """A malformed op or reference (not a gate matter)."""


# --------------------------------------------------------------------------
# ratify/dismiss free-text matching
# --------------------------------------------------------------------------

_STOP = {
    "the", "a", "an", "of", "to", "into", "and", "or", "on", "for", "with",
    "as", "is", "are", "it", "its", "that", "this", "at", "in", "by", "be",
    "was", "were", "from",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP}


def match_ratify_target(target: str, proposals: list[Proposal]) -> tuple[list[str], list[str]]:
    """Match free-text `target` against pending proposals by token overlap.

    The target may name several proposals joined by ' and '; each part is
    matched independently (normalize, drop stopwords, score = overlap /
    |part tokens|; threshold: >=1 shared token and score >= 0.25; ties break
    to the earliest proposal).  Returns (matched proposal ids, unmatched
    part texts).
    """
    parts = [p.strip() for p in re.split(r"\s+and\s+", target) if p.strip()] or [target]
    matched: list[str] = []
    unmatched: list[str] = []
    for part in parts:
        tt = _tokens(part)
        best_id, best_score = None, 0.0
        for pr in sorted(proposals, key=lambda p: p.created_event):
            if pr.status != "pending" or pr.id in matched:
                continue
            pt = _tokens(pr.description + " " + pr.kind.replace("_", " "))
            ov = len(tt & pt)
            score = ov / max(1, len(tt))
            if ov >= 1 and score > best_score + 1e-9:
                best_id, best_score = pr.id, score
        if best_id is not None and best_score >= 0.25:
            matched.append(best_id)
        else:
            unmatched.append(part)
    return matched, unmatched


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------

@dataclass
class RunResult:
    state: RecordState
    trace: Trace
    summary: dict


class ReplayEngine:
    def __init__(self, pool: Pool, scenario: Scenario, policy: Policy,
                 snapshots: bool = True):
        self.pool = pool
        self.scenario = scenario
        self.policy = policy
        self.state = RecordState(pool)
        self.view = RecordView(self.state)
        self.trace = Trace(pool.id, scenario.id, policy.name, snapshots=snapshots)

    # -- public --------------------------------------------------------------

    def run(self) -> RunResult:
        self.policy.reset(self.pool, self.scenario)
        for ev in self.scenario.events:
            self._process(ev)
        self._finalize()
        return RunResult(self.state, self.trace, self._summary())

    # -- event loop ----------------------------------------------------------

    def _process(self, ev: Event) -> None:
        st = self.state
        st.event_index = ev.index
        self.trace.begin_event(self._event_dict(ev))

        # a finding's foreground/background status must be judged exactly as
        # the landing path judges it: an authored-foreground finding arriving
        # with no live sitting IS a background landing and must not break the
        # attention gap
        if ev.type == "finding":
            scientist_driven = not (ev.background
                                    or st.live_sitting_id is None)
        else:
            scientist_driven = ev.type in SCIENTIST_EVENT_TYPES
        if scientist_driven:
            self._settle_gap()

        handler = getattr(self, f"_ev_{ev.type}")
        handler(ev)

        self.trace.end_event(st.export())

    def _finalize(self) -> None:
        st = self.state
        self.trace.begin_event({"event": "END"})
        # scenario end implies attention lapse: the live sitting parks and files
        self._end_live_sitting("filed")
        # settle any trailing attention gap under the same documented rule
        self._settle_gap()
        self.trace.end_event(st.export())

    # -- individual events ---------------------------------------------------

    def _ev_session_start(self, ev: Event) -> None:
        st = self.state
        self._end_live_sitting("parked")
        key = (ev.anchor, st.day)
        eid = st.episodes.get(key)
        coalesced = eid is not None
        if eid is None:
            eid = st.new_id("E")
            st.episodes[key] = eid
        sid = st.new_id("T")
        st.sittings[sid] = Sitting(id=sid, episode_id=eid, anchor=ev.anchor,
                                   opened_day=st.day, opened_event=ev.index)
        st.live_sitting_id = sid
        st.sittings[sid].touched.append(ev.anchor)
        self.trace.note("sitting_opened", sitting=sid, episode=eid,
                        anchor=ev.anchor, coalesced=coalesced)
        self._moment("session_start", ev)

    def _ev_finding(self, ev: Event) -> None:
        st = self.state
        finding = self.pool.finding(ev.ref)
        background = ev.background or st.live_sitting_id is None
        sid = None if background else st.live_sitting_id
        rec = FindingRecord(id=ev.ref, landed_day=st.day, landed_event=ev.index,
                            background=background, sitting_id=sid)
        st.findings[ev.ref] = rec
        # runs land in the sediment at launch (design section 5); background
        # landings during absence keep content current at Class 0/1
        rec.citations.append({"stratum": "sediment", "question": None,
                              "row": None, "day": st.day, "revised": False,
                              "note": "landed"})
        if sid:
            st.sittings[sid].findings.append(ev.ref)
        self.trace.note("finding_landed", finding=ev.ref, background=background,
                        sitting=sid)
        self._moment("finding_landed", ev, finding=finding)

    def _ev_gesture(self, ev: Event) -> None:
        st = self.state
        finding = self.pool.finding(ev.target)
        sid = st.live_sitting_id
        if sid:
            st.sittings[sid].gestures.append({"verb": ev.verb, "target": ev.target,
                                              "event": ev.index})
        self._compile_gesture(ev, finding)
        self._moment("gesture", ev, finding=finding)

    def _ev_instruction(self, ev: Event) -> None:
        self._moment("instruction", ev)

    def _ev_ratify(self, ev: Event) -> None:
        st = self.state
        matched, unmatched = match_ratify_target(
            ev.target, list(st.proposals.values()))
        for pid in matched:
            pr = st.proposals[pid]
            pr.status = "accepted"
            pr.decided_day, pr.decided_event = st.day, ev.index
            self.trace.note("proposal_accepted", proposal=pid, cls=pr.cls,
                            kind=pr.kind, target_text=ev.target)
        for part in unmatched:
            self.trace.note("unmatched_ratify", part=part, target_text=ev.target)
        self._moment("ratified", ev, matched=tuple(matched))

    def _ev_dismiss(self, ev: Event) -> None:
        st = self.state
        matched, unmatched = match_ratify_target(
            ev.target, list(st.proposals.values()))
        for pid in matched:
            pr = st.proposals[pid]
            pr.status = "dismissed"
            pr.decided_day, pr.decided_event = st.day, ev.index
            self.trace.note("proposal_dismissed", proposal=pid, cls=pr.cls)
        for part in unmatched:
            self.trace.note("unmatched_dismiss", part=part, target_text=ev.target)
        self._moment("dismissed", ev, matched=tuple(matched))

    def _ev_distill(self, ev: Event) -> None:
        # policy first: it may finalize rows / raise proposals for this table
        self._moment("distill", ev)
        self._settle_routing(ev)

    def _ev_clock(self, ev: Event) -> None:
        st = self.state
        # attention lapsed: the live sitting files (holds evaporate)
        self._end_live_sitting("filed")
        st.day += ev.advance_days
        st.pending_gap_days += ev.advance_days
        self.trace.note("clock", advance_days=ev.advance_days, day=st.day)
        self._moment("clock", ev)

    # -- sittings ------------------------------------------------------------

    def _end_live_sitting(self, mode: str) -> None:
        st = self.state
        sid = st.live_sitting_id
        if sid is None:
            return
        sit = st.sittings[sid]
        sit.state = mode
        sit.closed_day, sit.closed_event = st.day, st.event_index
        sit.silent_filed = not sit.residue()
        # leftovers shelf: landed in this sitting, never pinned, never routed
        for fid in sit.findings:
            rec = st.findings[fid]
            routed = any(r.product == fid and r.status in ("pending", "accepted")
                         for r in st.routing_rows.values())
            if not rec.pinned and not routed:
                sit.leftovers.append(fid)
        if st.holds:
            self.trace.note("holds_evaporated", holds=sorted(st.holds),
                            sitting=sid)
            st.holds.clear()
        st.live_sitting_id = None
        self.trace.note("sitting_closed", sitting=sid, mode=mode,
                        silent=sit.silent_filed, leftovers=list(sit.leftovers))

    # -- gestures: mechanical semantics (the user's own acts; no ceremony) ----

    def _compile_gesture(self, ev: Event, finding) -> None:
        st = self.state
        verb = ev.verb
        if verb == "pin":
            st.salience[ev.target] = "pinned"
            if ev.target in st.findings:
                st.findings[ev.target].pinned = True
            self.trace.note("gesture_pin", target=ev.target)
        elif verb == "fade":
            if st.salience.get(ev.target) == "pinned":
                self.trace.note("gesture_fade_rejected", target=ev.target,
                                reason="pinned elements never fade")
                return
            st.salience[ev.target] = "faded"
            if ev.target in st.findings:
                st.findings[ev.target].faded = True
            # anything already drafted from it lets go: pending rows fade
            for row in st.routing_rows.values():
                if row.product == ev.target and row.status == "pending":
                    row.status = "faded"
                    row.settled_event = ev.index
            self.trace.note("gesture_fade", target=ev.target)
        elif verb == "hold":
            st.holds.append(ev.target)
            self.trace.note("gesture_hold", target=ev.target)
        elif verb in INVESTIGATION_VERBS:
            owner = (finding.questions[0] if finding.questions
                     else (st.sittings[st.live_sitting_id].anchor
                           if st.live_sitting_id else self.pool.question_ids[0]))
            iid = st.new_id("PI")
            scrutiny = verb in SCRUTINY_VERBS
            item = PlanItem(
                id=iid, owner=owner, kind=verb,
                text=ev.note or f"{verb} {ev.target}",
                target=ev.target, state="planned",   # the user's own intent
                provisional=scrutiny, origin="gesture",
                created_day=st.day, created_event=ev.index)
            item.history.append({"event": ev.index, "to": "planned"})
            st.plan_items[iid] = item
            if scrutiny and ev.target in st.findings:
                st.findings[ev.target].provisional_open.append(iid)
            self.trace.note("gesture_plan_item", verb=verb, target=ev.target,
                            item=iid, owner=owner, provisional=scrutiny)
        elif verb == "draft_claim":
            # invokes the record-writer: drafting is the POLICY's job (the
            # draft is a decision and enters the consent queue via Propose)
            self.trace.note("gesture_draft_claim", target=ev.target)

    # -- distill: settle the routing table ------------------------------------

    def _settle_routing(self, ev: Event) -> None:
        """v1.1 consent semantics: distill accepts the routine (veto-tier)
        routing rows presented — ALL pending rows, by place, not by episode.
        Decisions (claim drafts, addenda, Class-2/3 proposals) stay in the
        consent queue for explicit ratify."""
        st = self.state
        settled = []
        for row in sorted(st.routing_rows.values(), key=lambda r: r.created_event):
            if row.status != "pending":
                continue
            row.status = "accepted"
            row.settled_event = ev.index
            self._apply_row(row)
            settled.append(row.id)
        table = {
            "distill_event": ev.index,
            "day": st.day,
            "sitting": st.live_sitting_id,
            "rows_settled": settled,
            "decisions_pending": [p.id for p in
                                  sorted(st.proposals.values(),
                                         key=lambda p: p.created_event)
                                  if p.status == "pending"],
        }
        st.routing_tables.append(table)
        self.trace.note("distill_settled", **table)

    def _apply_row(self, row: RoutingRow) -> None:
        st = self.state
        rec = st.findings.get(row.product)
        if rec is not None:
            for q in (row.questions or [None]):
                rec.citations.append({"stratum": row.stratum, "question": q,
                                      "row": row.id, "day": st.day,
                                      "revised": False, "note": row.note})
                if q is not None and row.stratum == "story":
                    root = st.sections[st.question_roots[q]]
                    if row.product not in root.cited:
                        root.cited.append(row.product)
            if rec.sitting_id:
                sit = st.sittings[rec.sitting_id]
                for q in row.questions:
                    sit.touched.append(q)
        # discharge subsumption: the row NAMES every item it closes; marks
        # clear only now, when the row is accepted
        for iid in row.discharges:
            item = st.plan_items.get(iid)
            if item is None:
                raise EngineError(f"row {row.id} discharges unknown item {iid}")
            item.state = "absorbed"
            item.discharged_by = row.id
            item.history.append({"event": st.event_index, "to": "absorbed",
                                 "via": row.id})
            if item.provisional:
                item.provisional = False
                tgt = st.findings.get(item.target)
                if tgt and iid in tgt.provisional_open:
                    tgt.provisional_open.remove(iid)
            self.trace.note("plan_item_discharged", item=iid, row=row.id,
                            target=item.target)

    # -- absence / timers ----------------------------------------------------

    def _settle_gap(self) -> None:
        """Settle the pending attention gap per the documented absence rule
        (module docstring): small gaps accrue to Class-2 timers, large gaps
        freeze them."""
        st = self.state
        gap = st.pending_gap_days
        if gap == 0:
            return
        st.pending_gap_days = 0
        if gap > ABSENCE_GAP_DAYS:
            st.absences.append({"days": gap, "ended_day": st.day,
                                "ended_event": st.event_index})
            self.trace.note("absence_gap_frozen", days=gap,
                            reason=f"gap > {ABSENCE_GAP_DAYS} days: governance "
                                   f"timers froze (14.7)")
            return
        expired = []
        for pr in sorted(st.proposals.values(), key=lambda p: p.created_event):
            if pr.status == "pending" and pr.cls == "2":
                pr.active_age += gap
                if pr.active_age >= CLASS2_EXPIRY_DAYS:
                    pr.status = "expired"
                    pr.decided_day, pr.decided_event = st.day, st.event_index
                    expired.append(pr.id)
                    self.trace.note("proposal_expired", proposal=pr.id,
                                    active_age=pr.active_age)
        self.trace.note("gap_counted", days=gap, expired=expired)
        self._last_expired = tuple(expired)

    # -- moments -------------------------------------------------------------

    def _moment(self, kind: str, ev: Event, finding=None,
                matched: tuple[str, ...] = ()) -> None:
        expired = getattr(self, "_last_expired", ())
        self._last_expired = ()
        moment = Moment(kind=kind, event=ev, state=self.view, pool=self.pool,
                        finding=finding, matched=matched, expired=expired)
        decided = self.policy.decide(moment)
        for op in decided:
            self._apply(op, consent=None)

    # -- THE GATE + op application -------------------------------------------

    def _effective_class(self, op: O.Op) -> str:
        """Static floor (ops.effective_class) plus the state-dependent floor:
        marking a finding superseded while live ratified/authored prose cites
        it is the Class-X interrupt, never a Class-1 citation touch-up."""
        eff = O.effective_class(op)
        if isinstance(op, O.MarkSuperseded) \
                and self._ratified_prose_cites(op.finding_id) \
                and O.CLASS_ORDER[eff] < O.CLASS_ORDER["X"]:
            eff = "X"
        return eff

    def _ratified_prose_cites(self, fid: str) -> bool:
        return any(p.superseded_by is None and (p.ratified or p.authored)
                   and fid in p.provenance
                   for p in self.state.prose.values())

    def _apply(self, op: O.Op, consent: Proposal | None):
        st = self.state
        eff = self._effective_class(op)
        # Gate 1 — consent conservation (on the EFFECTIVE class)
        if eff in ("2", "3", "X"):
            if consent is None or consent.status != "accepted":
                raise GateViolation(
                    f"consent conservation: class-{eff} op {op.kind} "
                    f"(declared class {op.cls}) applied without an accepted "
                    f"proposal")
            if O.CLASS_ORDER[consent.cls] < O.CLASS_ORDER[eff]:
                raise GateViolation(
                    f"consent ceiling: class-{eff} payload op {op.kind} "
                    f"exceeds its proposal's class {consent.cls} — a cheap "
                    f"wrapper cannot carry an expensive payload")
        # Gate 2 — authored-text immutability
        if isinstance(op, O.ReviseProse):
            block = st.prose.get(op.prose_id)
            if block is None:
                raise EngineError(f"revise_prose: unknown prose {op.prose_id}")
            if block.ratified or block.authored:
                raise GateViolation(
                    f"authored-text immutability: prose {op.prose_id} is "
                    f"ratified/authored; revision arrives only as a dated "
                    f"addendum proposal")

        handler = self._OP_HANDLERS[type(op)]
        result = handler(self, op, consent)
        self.trace.op("applied", op.to_dict(), result=result,
                      effective_cls=eff,
                      consent=consent.id if consent else None)
        return result

    # -- op handlers ----------------------------------------------------------

    def _op_create_section(self, op: O.CreateSection, consent):
        st = self.state
        existing = st.resolve_section(f"{op.question_id}/{op.title}")
        if existing is not None:
            return existing.id
        if op.question_id not in st.question_roots:
            raise EngineError(f"create_section: unknown question {op.question_id}")
        parent = (st.resolve_section(op.parent).id if op.parent
                  else st.question_roots[op.question_id])
        sid = st.new_id("S")
        sec = Section(id=sid, question_id=op.question_id, title=op.title,
                      kind=op.section_kind, parent_id=parent, intent=op.intent,
                      charge=op.charge, created_day=st.day,
                      created_event=st.event_index)
        sec.history.append({"event": st.event_index, "created": op.section_kind,
                            "cls": op.cls})
        st.sections[sid] = sec
        return sid

    def _op_promote_section(self, op: O.PromoteSection, consent):
        sec = self._section(op.section, "promote_section")
        if op.to_kind:
            sec.kind = op.to_kind
        if op.maturity:
            sec.maturity = op.maturity
        sec.history.append({"event": self.state.event_index, "promoted": True,
                            "to_kind": op.to_kind, "maturity": op.maturity,
                            "cls": op.cls})
        return sec.id

    def _op_demote_section(self, op: O.DemoteSection, consent):
        sec = self._section(op.section, "demote_section")
        if op.to == "appendix":
            sec.rank = "appendix"
        elif op.to == "closed":
            sec.status = "closed"
        elif op.to == "stub":
            sec.kind = "stub"
        else:
            raise EngineError(f"demote_section: bad target state {op.to!r}")
        sec.history.append({"event": self.state.event_index, "demoted": op.to,
                            "cls": op.cls})
        return sec.id

    def _op_merge_sections(self, op: O.MergeSections, consent):
        st = self.state
        sources = [self._section(ref, "merge_sections") for ref in op.sections]
        sid = st.new_id("S")
        merged = Section(id=sid, question_id=op.question_id, title=op.title,
                         kind="section",
                         parent_id=st.question_roots[op.question_id],
                         created_day=st.day, created_event=st.event_index)
        merged.history.append({"event": st.event_index,
                               "merged_from": [s.id for s in sources],
                               "cls": op.cls})
        st.sections[sid] = merged
        for s in sources:
            s.status = "merged"
            s.merged_into = sid          # identity kept; findable history
            for p in st.prose.values():
                if p.section_id == s.id:
                    p.section_id = sid
        return sid

    def _op_split_section(self, op: O.SplitSection, consent):
        st = self.state
        src = self._section(op.section, "split_section")
        new_ids = []
        for title in op.new_titles:
            sid = st.new_id("S")
            sec = Section(id=sid, question_id=src.question_id, title=title,
                          kind="section", parent_id=src.parent_id,
                          created_day=st.day, created_event=st.event_index)
            sec.history.append({"event": st.event_index, "split_from": src.id,
                                "cls": op.cls})
            st.sections[sid] = sec
            new_ids.append(sid)
        src.status = "merged"
        src.merged_into = new_ids[0]
        # like merge, split must not strand prose on a retired section:
        # the source's blocks reparent to the first child
        for p in st.prose.values():
            if p.section_id == src.id:
                p.section_id = new_ids[0]
        return new_ids

    def _op_write_prose(self, op: O.WriteProse, consent):
        st = self.state
        sec = self._section(op.section, "write_prose")
        pid = st.new_id("P")
        block = ProseBlock(id=pid, section_id=sec.id, text=op.text,
                           maturity=op.maturity,
                           provenance=list(op.provenance),
                           authored=op.authored, ratified=op.ratified,
                           created_day=st.day, created_event=st.event_index)
        st.prose[pid] = block
        return pid

    def _op_revise_prose(self, op: O.ReviseProse, consent):
        st = self.state
        old = st.prose[op.prose_id]          # existence checked at the gate
        pid = st.new_id("P")
        block = ProseBlock(id=pid, section_id=old.section_id, text=op.text,
                           maturity=op.maturity or old.maturity,
                           provenance=sorted(set(old.provenance) | set(op.provenance)),
                           revision_of=old.id,
                           created_day=st.day, created_event=st.event_index)
        st.prose[pid] = block
        old.superseded_by = pid              # revisions append; findable
        return pid

    def _op_add_addendum(self, op: O.AddAddendum, consent):
        st = self.state
        block = st.prose.get(op.prose_id)
        if block is None:
            raise EngineError(f"add_addendum: unknown prose {op.prose_id}")
        aid = st.new_id("A")
        st.addenda[aid] = Addendum(id=aid, prose_id=block.id, text=op.text,
                                   provenance=list(op.provenance),
                                   day=st.day, event=st.event_index)
        block.addenda.append(aid)
        block.contested = False              # the addendum resolves the flag
        return aid

    def _op_route_finding(self, op: O.RouteFinding, consent):
        st = self.state
        if op.finding_id not in st.findings:
            raise EngineError(f"route_finding: {op.finding_id} has not landed")
        for q in op.questions:
            if q not in st.question_roots:
                raise EngineError(f"route_finding: unknown question {q}")
        for iid in op.discharges:
            if iid not in st.plan_items:
                raise EngineError(f"route_finding: unknown discharge {iid}")
        rid = st.new_id("R")
        row = RoutingRow(id=rid, product=op.finding_id, stratum=op.stratum,
                         questions=list(op.questions), note=op.note,
                         discharges=list(op.discharges),
                         created_day=st.day, created_event=st.event_index,
                         sitting_id=st.live_sitting_id)
        st.routing_rows[rid] = row
        return rid

    def _op_create_plan_item(self, op: O.CreatePlanItem, consent):
        st = self.state
        iid = st.new_id("PI")
        scrutiny = op.item_kind in SCRUTINY_VERBS
        item = PlanItem(id=iid, owner=op.owner, kind=op.item_kind, text=op.text,
                        target=op.target, state=op.state, provisional=scrutiny,
                        origin="policy", created_day=st.day,
                        created_event=st.event_index)
        item.history.append({"event": st.event_index, "to": op.state})
        st.plan_items[iid] = item
        if scrutiny and op.target in st.findings:
            st.findings[op.target].provisional_open.append(iid)
        return iid

    def _op_advance_plan_item(self, op: O.AdvancePlanItem, consent):
        # NOTE (section 6 discharge subsumption): a direct advance — even to
        # 'absorbed' — does NOT clear a scrutiny item's provisional mark.
        # Marks clear only through an accepted RouteFinding row whose
        # `discharges` names the item (see _apply_row): subsumption is
        # proposed and consented, never asserted by fiat.
        st = self.state
        item = st.plan_items.get(op.item_id)
        if item is None:
            raise EngineError(f"advance_plan_item: unknown item {op.item_id}")
        item.state = op.to_state
        item.history.append({"event": st.event_index, "to": op.to_state})
        return item.id

    def _op_propose(self, op: O.Propose, consent):
        st = self.state
        # consent ceiling (propose-time half): the proposal's class must
        # dominate every payload op's effective class, evaluated against the
        # current state; the apply-time gate re-asserts per payload op.  The
        # free-text-description <-> payload semantic gap cannot be fully
        # closed here (ratify matches by description, which no gate can
        # verify against payload semantics) — this class ceiling is the
        # enforceable part.
        payload_classes = [self._effective_class(pop) for pop in op.payload]
        for pop, eff in zip(op.payload, payload_classes):
            if O.CLASS_ORDER[op.proposal_cls] < O.CLASS_ORDER[eff]:
                raise GateViolation(
                    f"consent ceiling: proposal class {op.proposal_cls} is "
                    f"below its payload op {pop.kind}'s effective class "
                    f"{eff} — consent must be sought at the payload's tier")
        pid = st.new_id("PR")
        pr = Proposal(id=pid, cls=op.proposal_cls, kind=op.proposal_kind,
                      description=op.description, payload=list(op.payload),
                      created_day=st.day, created_event=st.event_index)
        st.proposals[pid] = pr
        # a Class-X addendum proposal marks its target contested (the visible
        # condition: prose no longer reads at face value, text untouched)
        for pop in op.payload:
            if isinstance(pop, O.AddAddendum):
                block = st.prose.get(pop.prose_id)
                if block is not None:
                    block.contested = True
        self.trace.note("proposal_opened", proposal=pid, cls=pr.cls,
                        kind=pr.kind, description=pr.description,
                        payload_classes=payload_classes)
        return pid

    def _op_apply_consented(self, op: O.ApplyConsented, consent):
        st = self.state
        pr = st.proposals.get(op.proposal_id)
        if pr is None:
            raise EngineError(f"apply_consented: unknown proposal {op.proposal_id}")
        if pr.status == "expired":
            raise GateViolation(
                f"consent conservation: proposal {pr.id} expired — a lapsed "
                f"Class-2 proposal can never apply")
        if pr.status != "accepted":
            raise GateViolation(
                f"consent conservation: proposal {pr.id} is {pr.status}, "
                f"not accepted")
        if pr.applied:
            raise GateViolation(
                f"consent conservation: proposal {pr.id} already applied — "
                f"consent is spent once")
        pr.applied = True
        payload_classes = [self._effective_class(pop) for pop in pr.payload]
        results = [self._apply(pop, consent=pr) for pop in pr.payload]
        self.trace.note("proposal_applied", proposal=pr.id, cls=pr.cls,
                        payload_classes=payload_classes,
                        results=[str(r) for r in results])
        return pr.id

    def _op_withdraw_proposal(self, op: O.WithdrawProposal, consent):
        st = self.state
        pr = st.proposals.get(op.proposal_id)
        if pr is None:
            raise EngineError(f"withdraw_proposal: unknown {op.proposal_id}")
        if pr.status == "pending":
            pr.status = "withdrawn"
            pr.withdraw_reason = op.reason
            pr.decided_day, pr.decided_event = st.day, st.event_index
        return pr.id

    def _op_set_salience(self, op: O.SetSalience, consent):
        st = self.state
        if op.mark == "faded" and st.salience.get(op.target) == "pinned":
            self.trace.note("salience_rejected", target=op.target,
                            reason="pinned elements never fade")
            return None
        if op.mark is None:
            st.salience.pop(op.target, None)
        else:
            st.salience[op.target] = op.mark
            if op.mark == "held":
                st.holds.append(op.target)
        return op.target

    def _op_mark_superseded(self, op: O.MarkSuperseded, consent):
        st = self.state
        rec = st.findings.get(op.finding_id)
        if rec is None:
            raise EngineError(f"mark_superseded: {op.finding_id} not landed")
        rec.superseded_by = op.by
        rec.superseded_note = op.note
        for c in rec.citations:               # citations revised, never deleted
            c["revised"] = True
        self.trace.note("finding_superseded", finding=op.finding_id, by=op.by)
        return op.finding_id

    def _op_add_briefing(self, op: O.AddBriefing, consent):
        st = self.state
        st.briefings.append({"day": st.day, "event": st.event_index,
                             "text": op.text, "covers": list(op.covers)})
        return len(st.briefings) - 1

    _OP_HANDLERS = {
        O.CreateSection: _op_create_section,
        O.PromoteSection: _op_promote_section,
        O.DemoteSection: _op_demote_section,
        O.MergeSections: _op_merge_sections,
        O.SplitSection: _op_split_section,
        O.WriteProse: _op_write_prose,
        O.ReviseProse: _op_revise_prose,
        O.AddAddendum: _op_add_addendum,
        O.RouteFinding: _op_route_finding,
        O.CreatePlanItem: _op_create_plan_item,
        O.AdvancePlanItem: _op_advance_plan_item,
        O.Propose: _op_propose,
        O.ApplyConsented: _op_apply_consented,
        O.WithdrawProposal: _op_withdraw_proposal,
        O.SetSalience: _op_set_salience,
        O.MarkSuperseded: _op_mark_superseded,
        O.AddBriefing: _op_add_briefing,
    }

    # -- helpers -------------------------------------------------------------

    def _section(self, ref: str, opname: str) -> Section:
        sec = self.state.resolve_section(ref)
        if sec is None:
            raise EngineError(f"{opname}: cannot resolve section {ref!r}")
        return sec

    @staticmethod
    def _event_dict(ev: Event) -> dict:
        out = {"index": ev.index, "t": ev.t, "type": ev.type}
        for k in ("anchor", "ref", "verb", "target", "text", "note"):
            v = getattr(ev, k)
            if v is not None:
                out[k] = v
        if ev.background:
            out["background"] = True
        if ev.advance_days:
            out["advance_days"] = ev.advance_days
        return out

    def _summary(self) -> dict:
        st, tr = self.state, self.trace
        props = list(st.proposals.values())
        rows = list(st.routing_rows.values())
        return {
            "pool": self.pool.id,
            "scenario": self.scenario.id,
            "policy": self.policy.name,
            "events": len(self.scenario.events),
            "ops_applied": tr.count_ops("applied"),
            "proposals": {
                "opened": len(props),
                "accepted": sum(p.status == "accepted" for p in props),
                "expired": sum(p.status == "expired" for p in props),
                "dismissed": sum(p.status == "dismissed" for p in props),
                "withdrawn": sum(p.status == "withdrawn" for p in props),
                "applied": sum(p.applied for p in props),
            },
            "sections_created": sum(s.kind != "question-root"
                                    for s in st.sections.values()),
            "prose_blocks": len(st.prose),
            "addenda": len(st.addenda),
            "plan_items": len(st.plan_items),
            "plan_absorbed": sum(i.state == "absorbed"
                                 for i in st.plan_items.values()),
            "rows": {
                "settled": sum(r.status == "accepted" for r in rows),
                "pending": sum(r.status == "pending" for r in rows),
                "faded": sum(r.status == "faded" for r in rows),
            },
            "findings": {
                "foreground": sum(not f.background for f in st.findings.values()),
                "background": sum(f.background for f in st.findings.values()),
            },
            "sittings": {
                "count": len(st.sittings),
                "episodes": len(st.episodes),
                "silent": sum(s.silent_filed for s in st.sittings.values()),
            },
            "absences": [a["days"] for a in st.absences],
            "unmatched_ratifies": tr.count_notes("unmatched_ratify"),
            "digest": tr.run_digest(),
        }
