"""The five reference baselines (RUNNER_HANDOFF section 3).

Honest implementations, not strawmen: each does exactly what its one-line
description says, through legal ops only — a baseline that trips the gate is
a bug.  None of them reads instruction text or the `expect` fields (they are
dumb strategies; instructions require judgment they do not have).

Documented readings:

* `inert` — routes every foreground finding to sediment explicitly (the
  engine already lands findings in sediment at launch; the explicit row makes
  the routing table honest about the choice).  Never proposes, never writes,
  never applies.  Ratifies against it match nothing (recorded in the trace).

* `never_restructure` — writes prose and routes honestly but NEVER emits a
  structural op.  Reading: the state model gives every pool question a
  furniture `question-root` node at init; this policy writes question-keyed
  prose into those roots and cites findings under all their tagged questions
  (one entity, many citations), so it can be fully honest about content
  without ever creating/promoting/demoting/splitting/merging a section.  It
  proposes claim drafts when the scientist gestures draft_claim (drafting is
  content, not structure) and applies its own consented proposals.  It
  ignores `overturns` metadata entirely — revision is where it goes wrong.

* `append_to_first_question` — everything lands under the session anchor's
  section; untagged and cross-tagged findings alike are cited only under the
  anchor.  It creates one working stub per anchor question — declared Class 1
  (an additive, local, reversible container), the one legal way a baseline
  can "create sections freely" without self-consenting Class-2/3 changes;
  its failure mode under test is mis-ROUTING, not structural ceremony.
  Background findings (no sitting → no anchor) fall back to the first tagged
  question, else stay in sediment.

* `obey_overturn_labels` — never_restructure plus mechanical execution of
  revision whenever landed metadata carries `overturns` edges: it marks the
  overturned finding superseded (citations revised, never deleted) and
  revises every live prose block citing it.  It treats BOTH severity cases
  with the same mechanical rule — no severity judgment — but expresses the
  revision through the only legal channel in each case: where NO
  ratified/authored prose cites the overturned finding, the supersession
  mark and the prose revisions apply directly (Class 1); where ratified
  prose IS implicated, the supersession mark and the addenda ride ONE
  Class-X addendum PROPOSAL (the gate floors mark_superseded at 'X' there,
  and forbids rewriting) — pending until an explicit ratify, which the
  corpus streams mostly never supply.  Unratified blocks citing the same
  finding are still revised directly (legal at Class 1).  The channel choice
  is gate-shaped, not judgment-shaped: the policy still applies one rule to
  every overturns edge.  What it cannot fake: cascade re-examination of
  dependent claims, withdrawal of pending drafts, structural demotion — the
  corpus assertions that were sharpened to kill it.

* `ignore_weak` — never_restructure minus every weak-strength finding: no
  routing row, no prose, no claim draft (a draft_claim gesture aimed at a
  weak finding is ignored).  The finding still exists in the sediment —
  landing-at-launch is substrate, not policy — but the policy never carries
  it anywhere.
"""

from __future__ import annotations

from . import ops as O
from .events import Finding
from .policy import Moment, Policy


def _short(text: str, n: int = 160) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


class Inert(Policy):
    name = "inert"

    def decide(self, moment: Moment) -> list[O.Op]:
        if moment.kind == "finding_landed" and not moment.event.background:
            return [O.RouteFinding(finding_id=moment.finding.id,
                                   stratum="sediment",
                                   note="inert: everything to sediment")]
        return []


class NeverRestructure(Policy):
    name = "never_restructure"

    # -- content honesty, zero structure ------------------------------------

    def decide(self, moment: Moment) -> list[O.Op]:
        if moment.kind == "finding_landed":
            return self._on_finding(moment)
        if moment.kind == "gesture":
            return self._on_gesture(moment)
        if moment.kind == "ratified":
            return [O.ApplyConsented(proposal_id=pid) for pid in moment.matched]
        return []

    def _on_finding(self, moment: Moment) -> list[O.Op]:
        f = moment.finding
        if moment.event.background:
            return []                      # absence: sediment only (14.7)
        if not self._carries(f):
            return []
        if f.questions:
            return [
                O.RouteFinding(finding_id=f.id, stratum="story",
                               questions=f.questions,
                               note="cited under all tagged questions"),
                O.WriteProse(section=f.questions[0],
                             text=f"{f.id}: {_short(f.claim)}",
                             maturity=O.strength_to_maturity(f.strength),
                             provenance=(f.id,)),
            ]
        return [O.RouteFinding(finding_id=f.id, stratum="notes",
                               note="untagged: to notes")]

    def _on_gesture(self, moment: Moment) -> list[O.Op]:
        ev, f = moment.event, moment.finding
        if ev.verb == "draft_claim" and self._carries(f):
            section = f.questions[0] if f.questions else (moment.state.anchor
                                                          or moment.pool.question_ids[0])
            return [O.Propose(
                proposal_kind="claim_draft",
                proposal_cls=O.CLAIM_DRAFT_PROPOSAL_CLS,
                description=f"claim draft on {f.id}: {_short(f.claim)}",
                payload=(O.WriteProse(
                    section=section,
                    text=f"Ratified claim ({f.id}): {_short(f.claim, 200)}",
                    maturity=O.strength_to_maturity(f.strength),
                    provenance=(f.id,), ratified=True),),
            )]
        if ev.verb == "pin" and f is not None and not f.questions \
                and self._carries(f):
            # a pin must be routed; the agent proposes the destination visibly
            return [O.RouteFinding(finding_id=f.id, stratum="notes",
                                   note="pinned tangential: to notes")]
        return []

    @staticmethod
    def _carries(f: Finding | None) -> bool:
        return f is not None


class AppendToFirstQuestion(Policy):
    name = "append_to_first_question"

    def decide(self, moment: Moment) -> list[O.Op]:
        if moment.kind == "finding_landed":
            f = moment.finding
            anchor = moment.state.anchor
            if moment.event.background or anchor is None:
                if f.questions:
                    return [O.RouteFinding(finding_id=f.id, stratum="story",
                                           questions=(f.questions[0],),
                                           note="background: first tagged question")]
                return []
            title = f"{anchor} working section"
            return [
                O.CreateSection(question_id=anchor, title=title,
                                section_kind="stub", cls="1"),
                O.RouteFinding(finding_id=f.id, stratum="story",
                               questions=(anchor,),
                               note="everything under the anchor"),
                O.WriteProse(section=f"{anchor}/{title}",
                             text=f"{f.id}: {_short(f.claim)}",
                             maturity=O.strength_to_maturity(f.strength),
                             provenance=(f.id,)),
            ]
        if moment.kind == "gesture" and moment.event.verb == "draft_claim":
            f = moment.finding
            anchor = moment.state.anchor or (f.questions[0] if f.questions
                                             else moment.pool.question_ids[0])
            return [O.Propose(
                proposal_kind="claim_draft",
                proposal_cls=O.CLAIM_DRAFT_PROPOSAL_CLS,
                description=f"claim draft on {f.id}: {_short(f.claim)}",
                payload=(O.WriteProse(
                    section=anchor,
                    text=f"Ratified claim ({f.id}): {_short(f.claim, 200)}",
                    maturity=O.strength_to_maturity(f.strength),
                    provenance=(f.id,), ratified=True),),
            )]
        if moment.kind == "ratified":
            return [O.ApplyConsented(proposal_id=pid) for pid in moment.matched]
        return []


class ObeyOverturnLabels(NeverRestructure):
    name = "obey_overturn_labels"

    def _on_finding(self, moment: Moment) -> list[O.Op]:
        ops = super()._on_finding(moment)
        f = moment.finding
        for old in f.overturns:
            if moment.state.finding_record(old) is None:
                continue                    # not landed in this ordering
            live = [b for b in moment.state.prose_blocks(citing=old)
                    if b["superseded_by"] is None]
            ratified = [b for b in live if b["ratified"] or b["authored"]]
            mark = O.MarkSuperseded(
                finding_id=old, by=f.id,
                note="mechanical: overturns edge in pool metadata")
            if ratified:
                # ratified prose implicated: the mark and the addenda ride
                # one Class-X proposal (the legal channel; same mechanical
                # decision, no severity judgment)
                addenda = tuple(O.AddAddendum(
                    prose_id=b["id"],
                    text=f"Addendum: {old}'s reading is superseded by {f.id}.",
                    provenance=(f.id, old)) for b in ratified)
                ops.append(O.Propose(
                    proposal_kind="addendum",
                    proposal_cls="X",
                    description=(f"addendum: {old} superseded by {f.id} "
                                 f"— {_short(f.claim, 100)}"),
                    payload=addenda + (mark,),
                ))
            else:
                ops.append(mark)
            for block in live:
                if block["ratified"] or block["authored"]:
                    continue                # handled via the addendum channel
                ops.append(O.ReviseProse(
                    prose_id=block["id"],
                    text=(f"Revised: {old} superseded by {f.id} — "
                          f"{_short(f.claim, 140)}"),
                    provenance=(f.id,)))
        return ops


class IgnoreWeak(NeverRestructure):
    name = "ignore_weak"

    def _on_finding(self, moment: Moment) -> list[O.Op]:
        if moment.finding.strength == "weak":
            return []                       # dropped entirely
        return super()._on_finding(moment)

    def _on_gesture(self, moment: Moment) -> list[O.Op]:
        f = moment.finding
        if f is not None and f.strength == "weak":
            return []
        return super()._on_gesture(moment)


BASELINES: dict[str, type[Policy]] = {
    Inert.name: Inert,
    NeverRestructure.name: NeverRestructure,
    AppendToFirstQuestion.name: AppendToFirstQuestion,
    ObeyOverturnLabels.name: ObeyOverturnLabels,
    IgnoreWeak.name: IgnoreWeak,
}
