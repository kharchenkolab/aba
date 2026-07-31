"""Typed op vocabulary — the policy -> engine contract.

Every op carries a governance class `cls` in {'0','1','2','3','X'}
(RECORD_DESIGN section 14.1).  THE GATE (engine.py) enforces:

* any op with cls in {'2','3','X'} applies ONLY as the payload of an accepted
  proposal (Propose -> ratify -> ApplyConsented).  A policy that emits such an
  op directly trips consent conservation.
* prose flagged ratified/authored is never rewritten (ReviseProse on it is a
  violation even inside a consented payload); revision arrives only as a
  dated, provenance-linked AddAddendum inside an accepted proposal.

Default classes (a policy may choose a stricter class, never a looser one in
spirit — the gate only checks the class the op declares, grading (S3) judges
whether the declared class was honest):

  create_section(stub)      '1'  additive, local, reversible
  create_section(section)   '2'  a tier presence on the page
  promote_section           '2'  (Class 3 when it is a cross-question /
                                  arc-level promotion — policy declares)
  demote_section            '3'  appendixing / closing touches the synthesis
  merge/split               '2'  within a question; '3' across
  write_prose               '1'  in-slot prose (ratified/authored writes are
                                  decisions: consent required regardless)
  revise_prose              '1'  legal only on non-ratified, non-authored prose
  add_addendum              'X'  the interrupt grammar; always via consent
  route_finding             '1'  routine, veto-tier routing row
  create/advance_plan_item  '0'  the plan is ceremony-free
  propose                   '0'  raising a proposal is always allowed
  apply_consented           '0'  (its payload ops carry their own classes)
  set_salience              '1'
  mark_superseded           '1'  citation-level revision (never deletes)
  withdraw_proposal         '0'
  add_briefing              '1'

Section references: ops address sections either by raw section id (as read
from the state view) or by a *section ref* string — `"Q1"` means the question
root node of Q1, `"Q1/Some title"` means the titled section under Q1 (resolved
at apply time, so a create_section earlier in the same op batch is visible).

Extra ops beyond the RUNNER_HANDOFF minimum (recorded per instructions):
MarkSuperseded, WithdrawProposal, AddBriefing.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

CLASSES = ("0", "1", "2", "3", "X")
MATURITIES = ("conjecture", "supported", "cross-checked", "robust")
STRATA = ("story", "notes", "sediment")
PLAN_STATES = ("proposed", "planned", "taken-up", "produced", "absorbed")
PLAN_KINDS = ("check", "corroborate", "alternatives", "expand", "plan", "analysis")
SALIENCE_MARKS = ("pinned", "faded", "held", None)


def strength_to_maturity(strength: str) -> str:
    return {"weak": "conjecture", "moderate": "supported",
            "strong": "cross-checked"}.get(strength, "conjecture")


class Op:
    """Base for all ops.  Concrete ops are dataclasses; each declares its own
    `cls` field (with the documented default) as its last field."""

    kind = "op"
    cls: str = "1"

    def to_dict(self) -> dict:
        out = {"op": self.kind}
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            out[f.name] = _plain(v)
        return out


def _plain(v):
    if isinstance(v, Op):
        return v.to_dict()
    if isinstance(v, (tuple, list)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in sorted(v.items())}
    return v


# --- structure -------------------------------------------------------------

@dataclass
class CreateSection(Op):
    kind = "create_section"
    question_id: str
    title: str
    section_kind: str = "stub"          # 'stub' | 'section'
    parent: str | None = None           # section ref of parent (default: root)
    intent: bool = False                # intent-marked (section 14.5)
    charge: str = ""
    cls: str = "1"


@dataclass
class PromoteSection(Op):
    kind = "promote_section"
    section: str                        # section ref or id
    to_kind: str | None = None          # e.g. 'section'
    maturity: str | None = None
    cls: str = "2"


@dataclass
class DemoteSection(Op):
    kind = "demote_section"
    section: str
    to: str = "appendix"                # 'stub' | 'appendix' | 'closed'
    cls: str = "3"


@dataclass
class MergeSections(Op):
    kind = "merge_sections"
    sections: tuple[str, ...]
    question_id: str
    title: str
    cls: str = "2"


@dataclass
class SplitSection(Op):
    kind = "split_section"
    section: str
    new_titles: tuple[str, ...]
    cls: str = "2"


# --- prose -----------------------------------------------------------------

@dataclass
class WriteProse(Op):
    kind = "write_prose"
    section: str                        # section ref or id
    text: str
    maturity: str = "conjecture"
    provenance: tuple[str, ...] = ()
    authored: bool = False              # scientist-authored
    ratified: bool = False
    cls: str = "1"


@dataclass
class ReviseProse(Op):
    kind = "revise_prose"
    prose_id: str
    text: str
    maturity: str | None = None
    provenance: tuple[str, ...] = ()
    cls: str = "1"


@dataclass
class AddAddendum(Op):
    kind = "add_addendum"
    prose_id: str
    text: str
    provenance: tuple[str, ...] = ()
    cls: str = "X"


# --- routing / plan --------------------------------------------------------

@dataclass
class RouteFinding(Op):
    kind = "route_finding"
    finding_id: str
    stratum: str                        # 'story' | 'notes' | 'sediment'
    questions: tuple[str, ...] = ()     # cited under each; () for notes/sediment
    note: str = ""
    discharges: tuple[str, ...] = ()    # plan-item ids this landing closes
                                        # (discharge subsumption: the row NAMES
                                        # every item; marks clear on acceptance)
    cls: str = "1"


@dataclass
class CreatePlanItem(Op):
    kind = "create_plan_item"
    owner: str                          # question id (or section ref)
    item_kind: str = "plan"
    text: str = ""
    target: str | None = None           # finding the scrutiny is aimed at
    state: str = "proposed"             # gestures compile user items 'planned'
    cls: str = "0"


@dataclass
class AdvancePlanItem(Op):
    kind = "advance_plan_item"
    item_id: str
    to_state: str
    cls: str = "0"


# --- consent ---------------------------------------------------------------

@dataclass
class Propose(Op):
    kind = "propose"
    proposal_kind: str                  # 'claim_draft' | 'addendum' |
                                        # 'restructuring' | 'merge' | ...
    description: str                    # matched by ratify/dismiss free text
    payload: tuple[Op, ...] = ()
    proposal_cls: str = "2"             # the class of what is proposed
    cls: str = "0"


@dataclass
class ApplyConsented(Op):
    kind = "apply_consented"
    proposal_id: str
    cls: str = "0"


@dataclass
class WithdrawProposal(Op):
    kind = "withdraw_proposal"
    proposal_id: str
    reason: str = ""
    cls: str = "0"


# --- salience / history / briefing ----------------------------------------

@dataclass
class SetSalience(Op):
    kind = "set_salience"
    target: str
    mark: str | None                    # 'pinned' | 'faded' | 'held' | None
    cls: str = "1"


@dataclass
class MarkSuperseded(Op):
    kind = "mark_superseded"
    finding_id: str
    by: str
    note: str = ""
    cls: str = "1"


@dataclass
class AddBriefing(Op):
    kind = "add_briefing"
    text: str
    covers: tuple[str, ...] = ()
    cls: str = "1"
