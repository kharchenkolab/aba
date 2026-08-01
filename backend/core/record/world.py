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
_PROSE_BODY_KEY: str = ""
_CLAIM_STATEMENT_KEY: str = ""
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
                          maturity_key: str = "",
                          prose_body_key: str = "",
                          claim_statement_key: str = "") -> None:
    """Content-pack registration: map Record roles to this pack's entity-type
    names, order the claim maturity ladder (index = rung), name the artifact
    types the leftovers shelf sweeps (the pack typically derives these from
    its registry's `is_artifact` capability), and — when the pack keeps its
    ladder in entity metadata rather than the platform status column — the
    metadata key that carries it (e.g. "confidence"). `prose_body_key` names
    the metadata key holding a prose entity's readable body (e.g. "text");
    the story stratum renders it, so without it prose projects titles-only.
    `claim_statement_key` names the metadata key holding a claim's FULL
    assertion (packs often truncate the display title) — the story stratum
    drafts from statements, never from truncated titles.
    Re-registration replaces (same semantics as the type registry)."""
    global _ROLES, _MATURITY, _ARTIFACT_TYPES, _MATURITY_KEY, \
        _PROSE_BODY_KEY, _CLAIM_STATEMENT_KEY
    _ROLES = dict(roles)
    _MATURITY = tuple(maturity_order)
    _ARTIFACT_TYPES = tuple(artifact_types)
    _MATURITY_KEY = maturity_key
    _PROSE_BODY_KEY = prose_body_key
    _CLAIM_STATEMENT_KEY = claim_statement_key


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
        if _CLAIM_STATEMENT_KEY:
            stmt = md.get(_CLAIM_STATEMENT_KEY)
            if stmt and stmt != row["title"]:
                row["statement"] = stmt
        claims.append(row)

    prose = []
    superseded: dict[str, str] = {}   # old id -> the revision that replaced it
    prose_entities = _of_role("prose")
    prose_ids = {e["id"] for e in prose_entities}
    for e in prose_entities:
        row = dict(_slim(e), questions=_question_ref(e, qids))
        # the story stratum renders prose BODIES — metadata text, not
        # artifact bytes, so the projection contract holds
        if _PROSE_BODY_KEY:
            body = (e.get("metadata") or {}).get(_PROSE_BODY_KEY)
            if body:
                row["body"] = body
        # citations: which claims this prose absorbs (their chips retire)
        cites = (e.get("metadata") or {}).get("cites")
        if cites:
            row["cites"] = list(cites)
        # revision provenance: a wasDerivedFrom edge onto another prose
        # entity marks THAT one superseded — never rewritten, never deleted
        for ed in edges_from(e["id"]):
            if ed["rel_type"] == "wasDerivedFrom" and \
                    ed["target_id"] in prose_ids:
                row["revises"] = ed["target_id"]
                superseded[ed["target_id"]] = e["id"]
        prose.append(row)
    # heads carry their chain length; questions cite heads only
    def _versions(pid: str) -> int:
        n, seen = 1, {pid}
        row = next(p for p in prose if p["id"] == pid)
        while row.get("revises") and row["revises"] not in seen:
            seen.add(row["revises"])
            n += 1
            row = next((p for p in prose if p["id"] == row["revises"]), {})
            if not row:
                break
        return n
    for p in prose:
        if p["id"] not in superseded:
            v = _versions(p["id"])
            if v > 1:
                p["versions"] = v
    # a note carrying `sitting_of` is a DISTILLATION record — the moment a
    # sitting owns one it becomes an entity and its boundary freezes
    # (record-face invariant); it leaves the loose-notes stream
    note_entities = _of_role("note")
    distills = [e for e in note_entities
                if (e.get("metadata") or {}).get("sitting_of")]
    notes = [dict(_slim(e), questions=_question_ref(e, qids))
             for e in note_entities if e not in distills]

    q_rows = []
    for q in questions:
        row = _slim(q, "question", "open_questions", "lifecycle")
        row["claims"] = [c["id"] for c in claims if q["id"] in c["questions"]]
        # the section reads HEADS only; superseded prose stays in the rows
        # (provenance, search) but leaves the reading surface
        row["prose"] = [p["id"] for p in prose
                        if q["id"] in p["questions"]
                        and p["id"] not in superseded]
        # the org axis is recursive: a question whose parent is itself a
        # question is a subquestion (platform parent_entity_id column —
        # threads carry no edges). A parent outside the question set is
        # not a tree edge; the row stays top-level.
        parent = q.get("parent_entity_id")
        if parent and parent in qids:
            row["parent"] = parent
        q_rows.append(row)

    runs = list_runs(limit=sediment_limit)

    # frozen sittings first: each distillation names its runs and wears the
    # human label; owned runs leave the clustering pool, so heuristics
    # never redraw a frozen boundary
    run_by_id = {r["run_id"]: r for r in runs}
    frozen, owned = [], set()
    for e in distills:
        md = e.get("metadata") or {}
        have = [rid for rid in (md.get("run_ids") or [])
                if rid in run_by_id]
        owned.update(have)
        times = sorted(t for t in
                       ((run_by_id[r].get("started_at") or
                         run_by_id[r].get("updated_at")) for r in have) if t)
        frozen.append({"id": e["id"], "thread_id": md["sitting_of"],
                       "run_ids": have,
                       "started_at": times[0] if times else None,
                       "ended_at": times[-1] if times else None,
                       "label": e.get("title"), "frozen": True})
    sittings = frozen + derive_sittings(
        [r for r in runs if r["run_id"] not in owned])
    sittings.sort(key=lambda s: (s["started_at"] or "", s["id"]))

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
        "sittings": sittings,
        "whats_new": events,
        "tray": list_proposals(status="pending"),
        "leftovers": _leftovers(),
    }
