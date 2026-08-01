"""Record drafting advisor — phase-3 slice (RECORD_DESIGN §13.2 item 7).

Deterministic v0 of the drafting-during-work role: after a turn, when a
thread holds claims but no narrative, propose drafting the story stub. The
proposal is a RECORD-WRITE kind — accepting it creates the narrative stub
entity (undoable), which the Record face then renders under the question.
The LLM drafting (S6 charter) later rides the same proposal kind; the
detector stays cheap and local.

Signature carries the claim count, so a dismissal stops re-nagging until
the world changes (another claim lands) — the proposals-store discipline.
"""
from __future__ import annotations

from core.graph.entities import find_entities
from core.graph.proposals_store import add_proposal

# This pack's claim ladder (claim.yaml metadata.confidence); positives in
# ladder order, then the terminal negatives — the draft reads strongest-first.
_LADDER = ("preliminary", "supported", "validated", "contested", "refuted")
_NEGATIVE = {"contested", "refuted"}


def _of_thread(entity_type: str, tid: str) -> list[dict]:
    return [e for e in find_entities(type=entity_type, include_archived=False,
                                     not_deleted=True)
            if (e.get("metadata") or {}).get("thread_id") == tid]


def _conf(c: dict) -> str:
    return ((c.get("metadata") or {}).get("confidence")
            or c.get("status") or "preliminary")


def compose_draft(claims: list[dict]) -> str:
    """Weave the thread's claims into a readable story draft — mechanical
    v0 (titles at their maturity, strongest first, negatives set apart).
    The scientist ratifies by accepting; LLM drafting rides the same
    payload later."""
    def rung(c):
        try:
            return _LADDER.index(_conf(c))
        except ValueError:
            return 0
    pos = sorted((c for c in claims if _conf(c) not in _NEGATIVE),
                 key=rung, reverse=True)
    neg = [c for c in claims if _conf(c) in _NEGATIVE]
    parts = []
    if pos:
        lead, rest = pos[0], pos[1:]
        parts.append(f"{lead['title']} ({_conf(lead)}).")
        if rest:
            parts.append("Also in hand: " + "; ".join(
                f"{c['title']} ({_conf(c)})" for c in rest) + ".")
    if neg:
        parts.append("Set aside: " + "; ".join(
            f"{c['title']} ({_conf(c)})" for c in neg) + ".")
    return " ".join(parts)


# Distilled from the S6 charter (record-eval/runner/llm_policy.py): prose
# tracks evidence — never ahead of it; the scientist ratifies, never the model.
_DRAFT_CHARTER = (
    "You draft the story paragraph of a scientist's lab-notebook Record. "
    "Weave the claims below into ONE short readable paragraph (2-4 "
    "sentences). Rules: prose tracks evidence — state each finding AT its "
    "given maturity, naming it in parentheses, never stronger; order for "
    "reading (chronology or mechanism), but the strongest finding must be "
    "unmistakably the center; set contested/refuted material apart at the "
    "end; no headings, no lists, no identifiers. Include NOTHING the "
    "listed claims do not state: no analysis, confirmation, or success "
    "language, no mechanisms or consequences of your own — connect the "
    "claims plainly and stop. Reply with the paragraph only.")


def _gate_draft(text: str, claims: list[dict]) -> str:
    """Mechanical acceptance gate on model output (the S6 lesson: sanitize
    edges, never trust shape): a usable draft is plain prose that names at
    least one maturity and leaks no internal ids."""
    text = (text or "").strip()
    if not text or text.startswith(("#", "-", "*")):
        return ""
    if not any(f"({_conf(c)})" in text for c in claims):
        return ""
    if any(t in text for t in ("thr_", "run_", "sit-", "prj_")):
        return ""
    return text


def llm_draft(claims: list[dict]) -> str:
    """LLM drafting behind the SAME proposal kind — flag-gated
    (RECORD_LLM_DRAFTS=1), empty on any failure so the deterministic
    composer always backstops it."""
    import os
    if os.environ.get("RECORD_LLM_DRAFTS") != "1":
        return ""
    try:
        from core.config import MODEL
        from core.llm import (_CC_MARKER_BLOCK, _wants_cc_marker,
                              sync_anthropic_client)
        rows = "\n".join(f"- {c['title']} ({_conf(c)})" for c in claims)
        system = ([dict(_CC_MARKER_BLOCK),
                   {"type": "text", "text": _DRAFT_CHARTER}]
                  if _wants_cc_marker() else _DRAFT_CHARTER)
        r = sync_anthropic_client().messages.create(
            model=MODEL, max_tokens=300, system=system,
            messages=[{"role": "user", "content": rows}])
        text = " ".join(b.text for b in r.content
                        if getattr(b, "type", "") == "text")
        return _gate_draft(text, claims)
    except Exception:  # noqa: BLE001 — advisory path; silence is the failure mode
        return ""


def _heads(narratives: list[dict]) -> list[dict]:
    """Narratives not superseded by a revision (wasDerivedFrom points FROM
    the revision AT the superseded one)."""
    from core.graph.edges import edges_to
    ids = {n["id"] for n in narratives}
    superseded = set()
    for n in narratives:
        for ed in edges_to(n["id"]):
            if ed["rel_type"] == "wasDerivedFrom" and ed["source_id"] in ids:
                superseded.add(n["id"])
    return [n for n in narratives if n["id"] not in superseded]


def review_thread(tid: str):
    """Propose a story draft when the record is behind the claims — a first
    draft when no narrative stands, a REVISION when claims have landed
    since the head was drafted (`drafted_claims` staleness)."""
    claims = _of_thread("claim", tid)
    if len(claims) < 2:
        return None
    text = llm_draft(claims) or compose_draft(claims)
    payload = {"title": "What we know so far", "text": text,
               "cites": [c["id"] for c in claims],
               "drafted_claims": len(claims)}
    heads = _heads(_of_thread("narrative", tid))
    if not heads:
        return add_proposal(
            thread_id=tid, kind="record_draft", advisor="record_drafter",
            headline=(f"the record is behind the work — draft the story "
                      f"for this line ({len(claims)} claims, no narrative "
                      f"yet)"),
            signature=f"record_draft:{tid}:{len(claims)}",
            payload=payload)
    head = heads[-1]
    seen = (head.get("metadata") or {}).get("drafted_claims")
    if seen is not None and len(claims) > seen:
        payload["revises"] = head["id"]
        payload["title"] = head.get("title") or payload["title"]
        return add_proposal(
            thread_id=tid, kind="record_draft", advisor="record_drafter",
            headline=(f"claims have landed since this story was drafted "
                      f"({len(claims)} now, {seen} then) — revise it"),
            signature=f"record_draft:{tid}:{len(claims)}",
            payload=payload)
    return None


# ---------------------------------------------------------------- question
# The default thread absorbs the user's whole first message as its question
# (live finding, record-face.md). The face's heading needs ONE crisp
# question; the distiller proposes it (kind "question" — accept rewrites
# with undo, question_source flips to guide).

_VERBATIM_LEN = 140


def _extract_question(text: str) -> str:
    """Mechanical distillation: the first sentence that asks — honest and
    dumb. No question mark, no fabrication (return empty, stay quiet)."""
    for part in text.replace("\n", " ").split("?"):
        part = part.strip()
        if not part:
            continue
        # take the trailing clause of the part (after the last full stop)
        q = part.split(". ")[-1].split("! ")[-1].strip()
        if q and len(q) <= _VERBATIM_LEN:
            return q[0].lower() + q[1:] + "?"
        return ""
    return ""


def _llm_distill(text: str) -> str:
    import os
    if os.environ.get("RECORD_LLM_DRAFTS") != "1":
        return ""
    try:
        from core.config import MODEL
        from core.llm import (_CC_MARKER_BLOCK, _wants_cc_marker,
                              sync_anthropic_client)
        charter = ("Distill the scientist's message into the ONE question "
                   "this line of inquiry asks. Reply with a single crisp "
                   "question (<=120 characters, ends with '?'), nothing "
                   "else. Use only what the message states.")
        system = ([dict(_CC_MARKER_BLOCK), {"type": "text", "text": charter}]
                  if _wants_cc_marker() else charter)
        r = sync_anthropic_client().messages.create(
            model=MODEL, max_tokens=120, system=system,
            messages=[{"role": "user", "content": text}])
        out = " ".join(b.text for b in r.content
                       if getattr(b, "type", "") == "text").strip()
        if out.endswith("?") and len(out) <= 160 and \
                not any(t in out for t in ("thr_", "run_", "sit-")):
            return out
    except Exception:  # noqa: BLE001 — advisory path
        pass
    return ""


def distill_question(tid: str):
    """Propose a crisp heading when the question is a verbatim paragraph."""
    from core.graph.entities import get_entity
    thr = get_entity(tid)
    if not thr:
        return None
    md = thr.get("metadata") or {}
    q = (md.get("question") or "").strip()
    if len(q) <= _VERBATIM_LEN and "\n" not in q:
        return None
    if md.get("question_source") == "guide":
        return None                      # already distilled, never re-nag
    crisp = _llm_distill(q) or _extract_question(q)
    if not crisp or crisp.rstrip("?").strip().lower() == \
            q.rstrip("?").strip().lower():
        return None
    return add_proposal(
        thread_id=tid, kind="question", advisor="record_drafter",
        headline=f"this line reads as your whole message — retitle it: "
                 f"“{crisp}”",
        signature=f"question_distill:{tid}:{len(q)}",
        payload={"question": crisp, "set_source": "guide"},
    )


def _on_stop_record(ctx: dict) -> None:
    tid = ctx.get("thread_id")
    if not tid:
        return
    from core import projects
    projects.spawn(review_thread, tid)
    projects.spawn(distill_question, tid)


from core.hooks.dispatcher import register as _register  # noqa: E402

_register("on_stop", _on_stop_record, priority=25)
