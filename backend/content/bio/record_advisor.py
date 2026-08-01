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


def _of_thread(entity_type: str, tid: str) -> list[dict]:
    return [e for e in find_entities(type=entity_type, include_archived=False,
                                     not_deleted=True)
            if (e.get("metadata") or {}).get("thread_id") == tid]


def review_thread(tid: str):
    """Propose a story stub when the record is behind the claims."""
    claims = _of_thread("claim", tid)
    if len(claims) < 2:
        return None
    if _of_thread("narrative", tid):
        return None
    return add_proposal(
        thread_id=tid, kind="record_draft", advisor="record_drafter",
        headline=(f"the record is behind the work — draft the story stub "
                  f"for this line ({len(claims)} claims, no narrative yet)"),
        signature=f"record_draft:{tid}:{len(claims)}",
        payload={"title": "What we know so far"},
    )


def _on_stop_record(ctx: dict) -> None:
    tid = ctx.get("thread_id")
    if not tid:
        return
    from core import projects
    projects.spawn(review_thread, tid)


from core.hooks.dispatcher import register as _register  # noqa: E402

_register("on_stop", _on_stop_record, priority=25)
