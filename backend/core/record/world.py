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

from datetime import datetime
from typing import Optional, Sequence

from core.graph.audit import list_events
from core.graph.entities import find_entities
from core.graph.edges import edges_from, edges_to
from core.graph.proposals_store import list_proposals
from core.graph.runs_port import list_runs

_ROLES: dict[str, str] = {}
_MATURITY: tuple[str, ...] = ()
_MATURITY_KEY: str = ""
_ARTIFACT_TYPES: tuple[str, ...] = ()

#: A sitting ends when attention moves — operationally, when the next run on
#: the same thread starts more than this many minutes after the last one.
SITTING_GAP_MINUTES = 45

#: An artifact is a leftover when nothing carries it: no includes/supports
#: edge in either direction and it isn't pinned (§13.1's edge-complement).
_CARRY_RELS = ("includes", "supports")


def register_record_roles(roles: dict[str, str],
                          maturity_order: Sequence[str] = (),
                          artifact_types: Sequence[str] = (),
                          maturity_key: str = "") -> None:
    """Content-pack registration: map Record roles to this pack's entity-type
    names, order the claim maturity ladder (index = rung), name the artifact
    types the leftovers shelf sweeps (the pack typically derives these from
    its registry's `is_artifact` capability), and — when the pack keeps its
    ladder in entity metadata rather than the platform status column — the
    metadata key that carries it (e.g. "confidence"). Re-registration
    replaces (same semantics as the type registry)."""
    global _ROLES, _MATURITY, _ARTIFACT_TYPES, _MATURITY_KEY
    _ROLES = dict(roles)
    _MATURITY = tuple(maturity_order)
    _ARTIFACT_TYPES = tuple(artifact_types)
    _MATURITY_KEY = maturity_key


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


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def derive_sittings(runs: list[dict],
                    gap_minutes: int = SITTING_GAP_MINUTES) -> list[dict]:
    """Cluster a project's runs into sittings — bounded episodes of work.

    Pure function over run rows (oldest first, as list_runs returns them).
    Per thread: a run starting more than `gap_minutes` after the previous
    run's last timestamp opens a new sitting; anything closer coalesces
    (micro-bursts are one episode). Runs with no thread are background
    landings, not sittings. A run with no parseable timestamp joins the
    current sitting (it cannot prove a gap). Boundaries derived here are
    provisional by design — once a sitting owns a distillation record it
    becomes an entity and the boundary freezes (RECORD_DESIGN §13.2.5).
    """
    by_thread: dict[str, list[dict]] = {}
    for r in runs:
        if r.get("thread_id"):
            by_thread.setdefault(r["thread_id"], []).append(r)

    out: list[dict] = []
    for tid, rows in by_thread.items():
        cur: Optional[dict] = None
        last_ts: Optional[datetime] = None
        for r in rows:
            start = _parse_ts(r.get("started_at")) or _parse_ts(r.get("updated_at"))
            gap = (start is not None and last_ts is not None
                   and (start - last_ts).total_seconds() > gap_minutes * 60)
            if cur is None or gap:
                cur = {"id": f"sit-{tid}-{len([s for s in out if s['thread_id'] == tid]) + 1}",
                       "thread_id": tid, "run_ids": [],
                       "started_at": r.get("started_at") or r.get("updated_at"),
                       "ended_at": None}
                out.append(cur)
            cur["run_ids"].append(r["run_id"])
            end = _parse_ts(r.get("updated_at")) or start
            if end is not None and (last_ts is None or end > last_ts):
                last_ts = end
            cur["ended_at"] = r.get("updated_at") or r.get("started_at") or cur["ended_at"]
        # threads interleave in `out`; order sittings project-wide by start
    out.sort(key=lambda s: (s["started_at"] or "", s["id"]))
    return out


def _leftovers() -> list[dict]:
    """Artifact entities nothing carries: no includes/supports edge in either
    direction, not pinned, not archived — the §13.1 edge-complement."""
    if not _ARTIFACT_TYPES:
        return []
    rows = []
    for e in find_entities(type_in=list(_ARTIFACT_TYPES),
                           include_archived=False, not_deleted=True):
        if e.get("pinned"):
            continue
        carried = any(ed["rel_type"] in _CARRY_RELS
                      for ed in edges_to(e["id"]))
        carried = carried or any(ed["rel_type"] in _CARRY_RELS
                                 for ed in edges_from(e["id"]))
        if not carried:
            rows.append({"id": e["id"], "type": e["type"],
                         "title": e.get("title"),
                         "created_at": e.get("created_at")})
    return rows


def assemble_world(*, sediment_limit: int = 200,
                   since: Optional[str] = None) -> dict:
    """One project's Record World. Read-only; call under a bound project.

    `since` (ISO timestamp) filters what's-new to events after the caller's
    last visit — the per-user cursor lives with the caller (the face keeps
    it client-side); the substrate stays cursor-free."""
    questions = _of_role("question")
    qids = {q["id"] for q in questions}

    claims = []
    # "claim" here is the Record ROLE name (this module's own vocabulary);
    # which entity type plays it arrives via register_record_roles.
    for e in _of_role("claim"):  # noqa: seam
        md = e.get("metadata") or {}
        row = _slim(e)
        # the ladder may live in metadata (maturity_key), not the platform
        # status column — the status column is lifecycle, not confidence
        row["maturity"] = (md.get(_MATURITY_KEY) if _MATURITY_KEY else None) \
            or e.get("status")
        row["rung"] = _rung(row["maturity"])
        row["questions"] = _question_ref(e, qids)
        row["supports"] = [ed["target_id"] for ed in edges_from(e["id"])
                           if ed["rel_type"] == "supports"]
        row["caveats"] = md.get("caveats") or []
        row["evidence"] = max(len(row["supports"]),
                              len(md.get("evidence_ids") or []))
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

    events = list_events(limit=100)
    if since:
        events = [e for e in events if (e.get("ts") or "") > since]

    from core.graph.entities import get_entity
    ws = get_entity("workspace")

    return {
        "version": 1,
        "project": {"title": (ws or {}).get("title")},
        "roles": record_roles(),
        "maturity_ladder": list(_MATURITY),
        "questions": q_rows,
        "claims": claims,
        "prose": prose,
        "notes": notes,
        "sediment": {"runs": runs},
        "sittings": derive_sittings(runs),
        "whats_new": events,
        "tray": list_proposals(status="pending"),
        "leftovers": _leftovers(),
    }
