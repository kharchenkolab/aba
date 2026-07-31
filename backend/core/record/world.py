"""The Record's World assembler — a read-only projection of the bound
project's entity graph into the shape the Record face renders
(RECORD_DESIGN.md §13.3, rollout phase 1).

Domain-neutral via the register seam: the content pack declares which of its
entity types play which Record role (`register_record_roles`) and how its
claim-status ladder orders into maturity rungs. Core knows roles, never type
names. All reads go through the graph read-port; this module writes nothing.

Roles (all optional — an unregistered role simply yields empty lists):
    question  the line-of-inquiry container (carries metadata question /
              open_questions / lifecycle)
    claim     the maturing assertion (its status IS the maturity ladder)
    prose     long-form narrative entities
    note      pinned notes / kept messages
"""
from __future__ import annotations

from typing import Optional, Sequence

from core.graph.entities import find_entities
from core.graph.edges import edges_from, edges_to
from core.graph.runs_port import list_runs

_ROLES: dict[str, str] = {}
_MATURITY: tuple[str, ...] = ()


def register_record_roles(roles: dict[str, str],
                          maturity_order: Sequence[str] = ()) -> None:
    """Content-pack registration: map Record roles to this pack's entity-type
    names, and order the claim-status ladder (index = maturity rung).
    Re-registration replaces (same semantics as the type registry)."""
    global _ROLES, _MATURITY
    _ROLES = dict(roles)
    _MATURITY = tuple(maturity_order)


def record_roles() -> dict[str, str]:
    return dict(_ROLES)


def _rung(status: Optional[str]) -> Optional[int]:
    """Maturity rung for a claim status, or None when the status is outside
    the registered ladder (renderer falls back to showing the raw status)."""
    try:
        return _MATURITY.index(status)
    except ValueError:
        return None


def _slim(e: dict, *md_keys: str) -> dict:
    """The projection contract is handles + small typed facts, never bytes
    (entity-model.md): id/title/status/actor/timestamps + named metadata."""
    md = e.get("metadata") or {}
    out = {
        "id": e["id"],
        "title": e.get("title"),
        "status": e.get("status"),
        "actor": e.get("actor"),
        "created_at": e.get("created_at"),
        "updated_at": e.get("updated_at"),
    }
    for k in md_keys:
        out[k] = md.get(k)
    return out


def _of_role(role: str) -> list[dict]:
    t = _ROLES.get(role)
    if not t:
        return []
    return find_entities(type=t, include_archived=False, not_deleted=True)


def _question_ref(e: dict, question_ids: set[str]) -> list[str]:
    """Which questions an entity bears on: outgoing edges into a question
    entity, plus metadata thread_id/question_id. The anchor is an address,
    not a container — multi-question references are expected."""
    refs = [ed["target_id"] for ed in edges_from(e["id"])
            if ed["target_id"] in question_ids]
    md = e.get("metadata") or {}
    for k in ("thread_id", "question_id"):
        v = md.get(k)
        if v in question_ids and v not in refs:
            refs.append(v)
    return refs


def assemble_world(*, sediment_limit: int = 200) -> dict:
    """One project's Record World. Read-only; call under a bound project."""
    questions = _of_role("question")
    qids = {q["id"] for q in questions}

    claims = []
    # "claim" here is the Record ROLE name (this module's own vocabulary);
    # which entity type plays it arrives via register_record_roles.
    for e in _of_role("claim"):  # noqa: seam
        row = _slim(e)
        row["rung"] = _rung(e.get("status"))
        row["questions"] = _question_ref(e, qids)
        row["supports"] = [ed["target_id"] for ed in edges_from(e["id"])
                           if ed["rel_type"] == "supports"]
        claims.append(row)

    prose = [dict(_slim(e), questions=_question_ref(e, qids))
             for e in _of_role("prose")]
    notes = [dict(_slim(e), questions=_question_ref(e, qids))
             for e in _of_role("note")]

    q_rows = []
    for q in questions:
        row = _slim(q, "question", "open_questions", "lifecycle")
        row["claims"] = [c["id"] for c in claims if q["id"] in c["questions"]]
        row["prose"] = [p["id"] for p in prose if q["id"] in p["questions"]]
        q_rows.append(row)

    runs = list_runs(limit=sediment_limit)

    return {
        "version": 1,
        "roles": record_roles(),
        "maturity_ladder": list(_MATURITY),
        "questions": q_rows,
        "claims": claims,
        "prose": prose,
        "notes": notes,
        "sediment": {"runs": runs},
        # OODA-2 organs — keys stable from day one, filled next pass:
        "sittings": [],
        "whats_new": [],
        "tray": [],
        "leftovers": [],
    }
