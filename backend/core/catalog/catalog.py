"""Catalog API over the entity store.

A capability entity's full spec (capabilities.md §4.2) lives in its
`metadata`; `title` mirrors the capability name for tree/search legibility.
Scope + lifecycle status live in metadata too (a dedicated column can come
later without changing this API). Discovery is a simple substring/tag match in
P0; BM25 retrieval (capabilities.md §9.2) arrives when the catalog grows past
~25 entries.
"""
from __future__ import annotations
import os
from typing import Optional

from core.data.handles import ExecContext
from core.graph.entities import create_entity, get_entity, list_entities, update_entity
from core.graph.audit import log_event

CAPABILITY = "capability"
REFERENCE = "reference"

# Seed providers (registered by content, e.g. content.bio.capabilities) run
# lazily the first time a project's catalog is queried and found empty. This is
# dependency inversion: content registers into core, not the reverse. Keyed by
# DB path so each project DB gets seeded once.
_seed_providers: list = []
_seeded_dbs: set[str] = set()


def register_seed_provider(fn) -> None:
    if fn not in _seed_providers:
        _seed_providers.append(fn)


def _ensure_seeded() -> None:
    if not _seed_providers:
        return
    from core.graph._schema import active_db_path
    key = str(active_db_path())   # context-bound path inside a turn, else global
    if key in _seeded_dbs:
        return
    # Mark first to avoid recursion (providers call resolve/register, which
    # call _ensure_seeded again).
    _seeded_dbs.add(key)
    for fn in _seed_providers:
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass


def _to_capability(entity: dict) -> dict:
    """Flatten an entity row into a capability dict (metadata + id)."""
    meta = dict(entity.get("metadata") or {})
    meta.setdefault("name", entity.get("title"))
    meta.setdefault("scope", "system")
    meta.setdefault("status", "published")
    meta["id"] = entity["id"]
    return meta


def _visible(cap: dict, ctx: Optional[ExecContext]) -> bool:
    """A capability is visible if it is system-scope or its scope is in the
    caller's scope chain. With no ctx, everything is visible (single-user)."""
    if ctx is None:
        return True
    scope = cap.get("scope", "system")
    return scope == "system" or scope in (ctx.scope_chain or [])


def register_capability(spec: dict) -> str:
    """Persist a capability spec as an entity. `spec` follows capabilities.md
    §4.2 (name, version, archetype, provisioning, scope, status, ...).
    Returns the entity id."""
    name = spec.get("name") or "unnamed-capability"
    meta = dict(spec)
    meta.setdefault("scope", "system")
    meta.setdefault("status", "published")
    return create_entity(entity_type=CAPABILITY, title=name, metadata=meta)


def _approval_mode() -> str:
    """Read dynamically so tests / deployments can flip it at runtime."""
    return os.environ.get("ABA_CAPABILITY_APPROVAL", "auto")


def capability_status(cap_id: str) -> Optional[str]:
    """The lifecycle status of a capability entity, or None if unknown."""
    ent = get_entity(cap_id)
    if ent is None or ent.get("type") != CAPABILITY:
        return None
    return (ent.get("metadata") or {}).get("status", "published")


def approve_capability(cap_id: str) -> Optional[dict]:
    """Flip a proposed capability to `published` and audit it. Returns the
    capability dict, or None if unknown."""
    ent = get_entity(cap_id)
    if ent is None or ent.get("type") != CAPABILITY:
        return None
    meta = dict(ent.get("metadata") or {})
    meta["status"] = "published"
    update_entity(cap_id, metadata=meta)
    log_event("capability_approved", entity_id=cap_id,
              title=meta.get("name") or ent.get("title"),
              detail={"mode": _approval_mode()})
    return _to_capability(get_entity(cap_id))


def propose_capability(spec: dict, ctx: Optional[ExecContext] = None) -> str:
    """Draft a capability from a discovery hit, in the `proposed` lifecycle
    state (capabilities.md §7.2), and audit it. In `auto` approval mode it is
    published immediately; in `ask` mode it waits for approve_capability.
    Returns the new capability's entity id."""
    meta = dict(spec)
    meta["status"] = "proposed"
    if "scope" not in meta:
        meta["scope"] = f"project:{ctx.project_id}" if (ctx and ctx.project_id) else "project"
    cap_id = register_capability(meta)
    log_event("capability_proposed", entity_id=cap_id, title=meta.get("name"),
              detail={"source": meta.get("source", "pypi"),
                      "version": meta.get("version"),
                      "provisioning": meta.get("provisioning"),
                      "scope": meta.get("scope")})
    if _approval_mode() == "auto":
        approve_capability(cap_id)
    return cap_id


def update_capability(name_or_id: str, spec: dict,
                      ctx: Optional[ExecContext] = None) -> Optional[str]:
    """Update an EXISTING capability's mutable fields (provisioning, source,
    summary, domain_tags, version, …) IN PLACE — keeping its id + published
    status. Returns the entity id, or None if not found / not writable.

    Only **project**- or **user**-scoped capabilities are updatable: those are
    agent-/operator-proposed and meant to be corrected. system/installation
    scope entries are curated catalog content and are left untouched (the caller
    should propose a differently-named variant, or override at
    ensure_capability time). This is what lets the agent fix a wrong git ref /
    source on a capability it just proposed, instead of being stuck with a stale
    entry it can't change."""
    cap = resolve_capability(name_or_id, ctx=ctx)
    cap_id = cap.get("id") if cap else None
    ent = get_entity(cap_id) if cap_id else None
    if ent is None or ent.get("type") != CAPABILITY:
        return None  # not found, or a file-backed collection (no entity to update)
    meta = dict(ent.get("metadata") or {})
    scope = str(meta.get("scope", "system"))
    if not (scope.startswith("project") or scope.startswith("user")):
        return None  # curated entry — don't clobber
    for k, v in spec.items():
        if k in ("name", "scope", "status"):  # identity/lifecycle stay put
            continue
        meta[k] = v
    meta["status"] = "published"
    update_entity(cap_id, metadata=meta)
    log_event("capability_updated", entity_id=cap_id, title=meta.get("name"),
              detail={"source": meta.get("source"), "provisioning": meta.get("provisioning")})
    return cap_id


def list_capabilities(
    query: Optional[str] = None,
    tags: Optional[list[str]] = None,
    ctx: Optional[ExecContext] = None,
) -> list[dict]:
    """Return visible capabilities, optionally filtered by a substring query
    (over name + summary + tags) and/or required domain tags."""
    _ensure_seeded()
    out: list[dict] = []
    q = (query or "").lower().strip()
    want_tags = set(tags or [])
    for ent in list_entities(type_filter=CAPABILITY):
        cap = _to_capability(ent)
        if not _visible(cap, ctx):
            continue
        cap_tags = set(cap.get("domain_tags") or [])
        if want_tags and not (want_tags & cap_tags):
            continue
        if q:
            hay = " ".join([
                str(cap.get("name", "")),
                str(cap.get("summary", "")),
                " ".join(cap_tags),
            ]).lower()
            if q not in hay:
                continue
        out.append(cap)
    return out


def _cap_doc_text(cap: dict) -> str:
    """Searchable text for one capability."""
    return " ".join([
        str(cap.get("name", "")),
        str(cap.get("summary", "")),
        " ".join(cap.get("domain_tags") or []),
        str(cap.get("archetype", "")),
    ])


def search_capabilities(
    query: Optional[str] = None,
    *,
    limit: int = 20,
    tags: Optional[list[str]] = None,
    ctx: Optional[ExecContext] = None,
) -> list[dict]:
    """Intent-ranked capability search. BM25 over name+summary+tags+archetype
    (multi-word / semantic queries: 'align rna-seq reads' surfaces salmon/STAR)
    unioned with substring matches (partial words / prefixes BM25's whole-token
    scoring would miss). BM25-ranked hits first, then substring-only matches.
    Empty query → all visible (tag-filtered) capabilities. This is what the
    list_capabilities tool calls when given a query — list_capabilities() above
    stays the plain substring/tag filter used internally and for browsing."""
    cands = list_capabilities(query=None, tags=tags, ctx=ctx)  # visible + tag-filtered
    # Merge file-backed collection capabilities — searchable without being
    # seeded as entities (collections.md). Tag-filter + visibility applied
    # the same way.
    from core.catalog.collections import collection_capabilities
    want_tags = set(tags or [])
    for cap in collection_capabilities():
        if not _visible(cap, ctx):
            continue
        if want_tags and not (want_tags & set(cap.get("domain_tags") or [])):
            continue
        cands.append(cap)

    q = (query or "").strip()
    if not q:
        return cands[:limit] if limit else cands

    from core.search import BM25
    by_id = {c["id"]: c for c in cands}
    idx = BM25((c["id"], _cap_doc_text(c)) for c in cands)
    ranked_ids = [i for i, _ in idx.search(q, limit=limit)]
    seen = set(ranked_ids)
    ql = q.lower()
    for c in cands:  # substring fallback for partial-word recall
        if c["id"] in seen:
            continue
        if ql in _cap_doc_text(c).lower():
            ranked_ids.append(c["id"])
            seen.add(c["id"])
    out = [by_id[i] for i in ranked_ids if i in by_id]
    return out[:limit] if limit else out


def resolve_capability(
    name_or_id: str,
    version: Optional[str] = None,
    ctx: Optional[ExecContext] = None,
) -> Optional[dict]:
    """Look up a capability by entity id or by name. Returns the flattened
    capability dict, or None. (`version` is accepted for the frozen signature;
    multi-version selection arrives with real materialization in P1.)"""
    ent = get_entity(name_or_id)
    if ent is not None and ent.get("type") == CAPABILITY:
        cap = _to_capability(ent)
        return cap if _visible(cap, ctx) else None
    _ensure_seeded()
    for cap in list_capabilities(ctx=ctx):
        if cap.get("name") == name_or_id:
            if version is None or str(cap.get("version")) == str(version):
                return cap
    # Fall back to file-backed collections. These aren't graph entities, so
    # a name match here covers the discovered-but-not-promoted case.
    from core.catalog.collections import find_collection_capability
    col = find_collection_capability(name_or_id)
    if col is not None and _visible(col, ctx):
        return col
    return None
