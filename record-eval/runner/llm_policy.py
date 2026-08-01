"""S6 — the prompted-LLM editorial policy (RUNNER_HANDOFF §S6).

The first draft of the Record's judgment half: a model behind the same
Policy interface every baseline uses. Flag-gated by construction — it is
registered only when a Claude subscription bearer is resolvable, and the
matrix never includes it (matrix runs BASELINES; grade/replay select it by
name `llm_v0`).

Isolation note: this is the ONE runner module allowed an SDK. It borrows
the backend's OAuth bearer resolution (core.llm) so tokens refresh the
same way the deployment's do; the runner core stays stdlib.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
from pathlib import Path

from . import ops as O
from .policy import Moment, Policy

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")

MODEL = os.environ.get("RECORD_LLM_MODEL", "claude-haiku-4-5-20251001")

# ---------------------------------------------------------------- op schema

_OP_CLASSES = [O.CreateSection, O.PromoteSection, O.DemoteSection,
               O.MergeSections, O.SplitSection, O.WriteProse, O.ReviseProse,
               O.AddAddendum, O.RouteFinding, O.CreatePlanItem,
               O.AdvancePlanItem, O.Propose, O.ApplyConsented,
               O.WithdrawProposal, O.SetSalience, O.MarkSuperseded,
               O.AddBriefing]
_BY_KIND = {c.kind: c for c in _OP_CLASSES}


def _schema_lines() -> str:
    """Self-describing op vocabulary — introspected, so it cannot drift."""
    out = []
    for c in _OP_CLASSES:
        fs = []
        for f in dataclasses.fields(c):
            d = ("" if f.default is dataclasses.MISSING
                 and f.default_factory is dataclasses.MISSING  # type: ignore
                 else "?")
            fs.append(f"{f.name}{d}")
        out.append(f'  {{"op": "{c.kind}", {", ".join(fs)}}}')
    return "\n".join(out)


CHARTER = """You are the editorial agent of the Record — a living lab
notebook over a long-running analysis project. At each editorial moment you
decide which typed ops to emit. The scientist ratifies; you propose.

Core rules (the gate enforces them — violations are discarded):
- Three strata: story (ratified narrative), notes (noticed, cheap), sediment
  (every run, automatic). Route each finding where it belongs: tagged
  findings are cited under ALL their questions (one citation each, no
  copies); untagged findings go to notes; background landings need nothing.
- Prose tracks evidence. Write section prose at the maturity the finding's
  strength supports (weak->conjecture, moderate->supported, strong->robust).
  NEVER write prose ahead of evidence; structure may track intent via plan
  items and stubs.
- Consent classes: 0/1 apply freely (visible). Class 2 must be PROPOSED
  (applies on accept, expires visibly). Class 3 (promote/demote/merge/split
  sections, claim drafts) waits for explicit ratification — propose it,
  then emit apply_consented ONLY at a ratified moment that matches it.
- A proposal's proposal_cls must DOMINATE every payload op: prose written
  toward the ratified story (claim drafts, addenda to committed text) is
  effectively class 3 — so any proposal carrying write_prose/add_addendum
  payload needs proposal_cls "3" (or "X" for contradiction interrupts).
  When unsure, use "3"; an under-classed proposal is rejected whole.
- NEVER emit class-2/3 ops directly (promote_section, demote_section,
  merge_sections, split_section, write_prose toward the story, add_addendum):
  they may appear ONLY inside a propose payload. The only class-3-adjacent
  op you emit at top level is apply_consented, and ONLY when the moment
  text contains an APPLY-ALLOWED line naming that exact proposal id. At
  every other moment apply_consented is forbidden — a pending proposal
  stays pending until the scientist's ratify matches it.
- Promotion timing: a question starts as a stub. Propose promoting it to a
  full section (class 3) once ~3 findings have landed under it — not
  before, and exactly once.
- Overturns: when a finding overturns another, mark_superseded the old
  reading and revise citations; NEVER delete. If committed/ratified prose
  asserts the old reading, the correction is an addendum PROPOSAL (class X
  interrupt severity), not an edit.
- Gestures: the engine already compiled the user's gesture into plan items
  or salience; your job is any follow-through (e.g. draft_claim -> propose
  a claim_draft with the prose payload).
- Scrutiny: check/corroborate/alternatives items mark their target
  provisional; when later evidence answers one, advance the item and name
  it in the routing row's `discharges` — which takes PLAN-ITEM ids (from
  OPEN PLAN ITEMS), never finding ids.
- Absence: while the scientist is away, only sediment/notes activity —
  hold structure; on their return a brief add_briefing of consequence.

Reply with ONLY a JSON array of op objects (possibly empty). Op vocabulary
(fields marked ? are optional; "payload" is a nested array of op objects;
section refs are question ids; class values are STRINGS "0"|"1"|"2"|"3"|"X"):
""" + _schema_lines()


# ------------------------------------------------------------- rendering

def _render(m: Moment) -> str:
    v, lines = m.state, []
    ev = m.event
    lines.append(f"MOMENT: {m.kind} (day {v.day})")
    d = {k: val for k, val in ev.__dict__.items() if val not in (None, (), [], "")}
    lines.append(f"EVENT: {json.dumps(d, default=str)}")
    if m.finding is not None:
        f = m.finding
        lines.append("FINDING: " + json.dumps({
            "id": f.id, "claim": f.claim, "strength": f.strength,
            "questions": list(f.questions), "depends_on": list(f.depends_on),
            "overturns": list(f.overturns)}))
        for o in f.overturns:
            rec = v.finding_record(o)
            if rec:
                lines.append(f"OVERTURNED {o}: " + json.dumps(rec, default=str))
    if m.matched:
        lines.append("MATCHED PROPOSALS: " + json.dumps([
            v.proposal(p) for p in m.matched], default=str))
        lines.append(f"APPLY-ALLOWED: {list(m.matched)} — you may emit "
                     "apply_consented for exactly these ids, now or never.")
    if m.expired:
        lines.append(f"EXPIRED: {list(m.expired)}")
    lines.append(f"ANCHOR: {v.anchor}")
    secs = []
    for q in v.question_ids():
        s = v.section(q)
        if s:
            secs.append({k: s.get(k) for k in
                         ("ref", "question_id", "status", "rank", "title")
                         if k in s} | {
                "prose": len(v.prose_blocks(section=q))
                if "section" in v.prose_blocks.__code__.co_varnames else None,
                "cites": s.get("cites")})
    lines.append("SECTIONS: " + json.dumps(secs, default=str))
    lines.append("PENDING PROPOSALS: " + json.dumps(
        v.pending_proposals(), default=str))
    lines.append("OPEN PLAN ITEMS: " + json.dumps(
        [i for i in v.plan_items()
         if i.get("state") in ("proposed", "planned", "taken-up")],
        default=str))
    return "\n".join(lines)


# ------------------------------------------------------------- parsing

def _parse_op(d: dict) -> O.Op | None:
    c = _BY_KIND.get(d.get("op", ""))
    if c is None:
        return None
    kw = {}
    for f in dataclasses.fields(c):
        if f.name not in d:
            continue
        val = d[f.name]
        if f.name == "payload" and isinstance(val, list):
            val = tuple(x for x in (_parse_op(p) for p in val
                                    if isinstance(p, dict)) if x)
        elif isinstance(val, list):
            val = tuple(val)
        kw[f.name] = val
    for k in ("cls", "proposal_cls"):
        if k in kw and kw[k] is not None:
            kw[k] = str(kw[k])          # models like integer classes; the
                                        # ladder is strings ("0".."3", "X")
    if c is O.Propose:
        # proposing is definitionally free; a class on the propose op itself
        # means the model meant the PROPOSAL's class (a grammar footgun —
        # noted for the phase-3 op design)
        if kw.get("cls") not in (None, "0") and "proposal_cls" not in kw:
            kw["proposal_cls"] = kw["cls"]
        kw["cls"] = "0"
        # the proposal's class must dominate its payload — computable, so
        # compute it (a production op-builder would too, not guess)
        pay = kw.get("payload") or ()
        if pay:
            ceil = max((O.effective_class(x) for x in pay),
                       key=lambda cc: O.CLASS_ORDER.get(cc, 0), default="0")
            cur = kw.get("proposal_cls", "2")
            if O.CLASS_ORDER.get(ceil, 0) > O.CLASS_ORDER.get(cur, 0):
                kw["proposal_cls"] = ceil
    try:
        return c(**kw)
    except TypeError:
        return None


def _parse(text: str) -> list[O.Op]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    return [op for op in (_parse_op(d) for d in arr if isinstance(d, dict))
            if op is not None]


# ------------------------------------------------------------- the policy

class LLMPolicy(Policy):
    name = "llm_v0"

    def __init__(self):
        if _BACKEND not in sys.path:
            sys.path.insert(0, _BACKEND)
        import anthropic
        from core.llm import _CC_MARKER_BLOCK, _oauth_bearer
        tok = _oauth_bearer()
        if not tok:
            raise RuntimeError("no subscription bearer — llm_v0 unavailable")
        self._client = anthropic.Anthropic(auth_token=tok)
        self._system = [dict(_CC_MARKER_BLOCK),
                        {"type": "text", "text": CHARTER,
                         "cache_control": {"type": "ephemeral"}}]
        self.calls = 0
        self.parse_failures = 0

    def decide(self, moment: Moment) -> list[O.Op]:
        prompt = _render(moment)
        resp = self._client.messages.create(
            model=MODEL, max_tokens=1500, system=self._system,
            messages=[{"role": "user", "content": prompt}])
        self.calls += 1
        text = "".join(b.text for b in resp.content if b.type == "text")
        ops = _parse(text)
        # sanitize: discharge refs must be existing plan-item ids — a wrong
        # id is an engine crash, not a graded violation (runner gap, noted)
        valid = {i.get("id") for i in moment.state.plan_items()}
        for op in ops:
            if isinstance(op, O.RouteFinding) and getattr(op, "discharges", None):
                op.discharges = tuple(i for i in op.discharges if i in valid)
        # apply_consented is contractually valid ONLY for this moment's
        # matched ids — enforce mechanically, as the baselines do
        ops = [op for op in ops
               if not isinstance(op, O.ApplyConsented)
               or op.proposal_id in moment.matched]
        # references must exist: unknown question/section refs are engine
        # crashes, not graded violations — drop or downgrade
        refs = set(moment.pool.question_ids)
        try:
            refs |= {s.get("ref") for s in moment.state.sections()}
        except Exception:
            pass
        def _ref_ok(op):
            for attr in ("question_id", "section", "owner"):
                v = getattr(op, attr, None)
                if v is not None and v not in refs:
                    return False
            return True
        cleaned = []
        for op in ops:
            if isinstance(op, O.RouteFinding):
                op.questions = tuple(q for q in (op.questions or ())
                                     if q in refs)
                if op.stratum == "story" and not op.questions:
                    op.stratum = "notes"
            if _ref_ok(op):
                cleaned.append(op)
        ops = cleaned
        # only proposals leave the policy at class >= 2 — the general form
        # of the charter rule, enforced mechanically (a production advisor
        # is shaped this way: it can only propose)
        ops = [op for op in ops
               if isinstance(op, (O.Propose, O.ApplyConsented))
               or O.CLASS_ORDER.get(O.effective_class(op), 0)
               < O.CLASS_ORDER["2"]]
        if not ops and "[]" not in text.replace(" ", ""):
            self.parse_failures += 1
        return ops
