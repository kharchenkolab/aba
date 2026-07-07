"""In-kernel `aba` library — Phase 1 (reads only).

Injected into the run_python / run_r kernel namespace at setup (see
`core/exec/kernels/jupyter.py::_aba_helpers_py`). It reads the project's entity
graph DIRECTLY from the project SQLite DB (path in ``$ABA_PROJECT_DB``), with
**no backend import** — the kernel is a separate subprocess and `core` is not on
its path, so `aba` stays self-contained (pure stdlib).

It mirrors the common read predicates of `core.graph.entities.find_entities` /
`get_entity`; `tests/test_aba_inkernel_reads.py` is a parity test that guards
against drift. Each call prints a ``[aba.<verb>]`` telemetry marker so the
regtest harness can measure library discovery/use from the run output.

Writes are deliberately NOT here — the contact-write path (create/promote/relate)
routes to the backend via intent-harvest (Phase 2); the kernel never writes the
graph directly. See `misc/tool_library.md` (aba-notes) for the design.

NB: NO `from __future__ import annotations` — this source is injected mid-cell (the
kernel setup cell) and mid-script (the stateless `script.py` preamble), where a
`__future__` import would be a SyntaxError. Hints use `Optional[...]` (runtime-safe on
3.9+), never `X | Y`, for the same reason.
"""
import json
import os
import sqlite3
from typing import Any, Optional


class _Aba:
    """The `aba` handle exposed in the kernel: entity-graph reads over the
    project DB. Constructed with an explicit db path (tests) or from
    ``$ABA_PROJECT_DB`` (kernel injection)."""

    def __init__(self, db: Optional[str] = None):
        self._db = db or os.environ.get("ABA_PROJECT_DB")
        self._n = 0  # local-ref counter for write intents (create → relate chaining)
        # Content populates these (seam-clean: core reads, content provides). A type in
        # _lifecycle_verbs must be created via its dedicated verb, not generic create()
        # — else the agent makes structurally-poor entities (a finding with no evidence,
        # a dataset with no file adoption). _extra_help is appended to help().
        self._lifecycle_verbs: dict = {}
        self._extra_help = ""

    # -- internal --------------------------------------------------------
    def _rows(self, q: str, args: list) -> list[dict]:
        if not self._db:
            raise RuntimeError(
                "aba: no project database bound (ABA_PROJECT_DB unset). "
                "This kernel was started without a project context."
            )
        c = sqlite3.connect(self._db)
        c.row_factory = sqlite3.Row
        try:
            out = []
            for r in c.execute(q, args).fetchall():
                d = dict(r)
                m = d.get("metadata")
                if isinstance(m, str) and m:
                    try:
                        d["metadata"] = json.loads(m)
                    except Exception:
                        pass
                out.append(d)
            return out
        finally:
            c.close()

    # -- read verbs ------------------------------------------------------
    def find(
        self,
        type: Optional[str] = None,  # noqa: A002 — mirrors find_entities' public name
        status: Optional[str] = None,
        contains: Optional[str] = None,
        include_archived: bool = False,
        limit: Optional[int] = 50,
        columns: Optional[list] = None,
    ) -> list[dict]:
        """Find entities in this project. Mirrors `find_entities`' common
        predicates — `type`, `status`, title substring `contains`,
        `include_archived` — newest first. Returns a list of dicts."""
        print(f"[aba.find] type={type!r} status={status!r} contains={contains!r}", flush=True)
        cols = ", ".join(columns) if columns else "id, type, title, status, created_at"
        q = f"SELECT {cols} FROM entities WHERE 1=1"
        a: list = []
        if type is not None:
            q += " AND type = ?"; a.append(type)
        if status is not None:
            q += " AND status = ?"; a.append(status)
        if not include_archived:
            q += " AND status != 'archived'"
        if contains:
            q += " AND lower(title) LIKE ?"; a.append(f"%{contains.lower()}%")
        q += " ORDER BY created_at DESC"
        if limit is not None:
            q += " LIMIT ?"; a.append(int(limit))
        return self._rows(q, a)

    def get(self, entity_id: str, fields: Optional[list] = None) -> Optional[dict]:
        """Read one entity by id (all fields, or the subset in `fields`)."""
        print(f"[aba.get] {entity_id}", flush=True)
        rows = self._rows("SELECT * FROM entities WHERE id = ? LIMIT 1", [entity_id])
        if not rows:
            return None
        d = rows[0]
        if fields:
            d = {k: d.get(k) for k in fields}
        return d

    def exists(self, **predicates: Any) -> bool:
        """True iff at least one entity matches (limit-1 `find`)."""
        return bool(self.find(limit=1, **predicates))

    def provenance(self, entity_id: str, direction: str = "up",
                   depth: int = 8, rels: Optional[list] = None) -> list[dict]:
        """Lineage walk over the entity graph. direction='up' → ancestors (what this
        was derived from / generated by); 'down' → descendants (what depends on it).
        Returns [{id, type, title, rel, depth}] newest-hop-last. Optional `rels`
        restricts edge types. Replaces get_provenance / get_dependents."""
        print(f"[aba.provenance] {entity_id} {direction} depth={depth}", flush=True)
        src, tgt = ("source_id", "target_id") if direction == "up" else ("target_id", "source_id")
        seen = {entity_id}
        frontier = [entity_id]
        out: list = []
        for d in range(max(1, int(depth))):
            if not frontier:
                break
            nxt = []
            for node in frontier:
                for r in self._rows(
                        f"SELECT {tgt} AS nid, rel_type FROM entity_edges WHERE {src} = ?", [node]):
                    nid = r["nid"]
                    if rels and r["rel_type"] not in rels:
                        continue
                    if nid in seen:
                        continue
                    seen.add(nid)
                    nxt.append(nid)
                    ent = self._rows("SELECT id, type, title FROM entities WHERE id = ? LIMIT 1", [nid])
                    out.append({**(ent[0] if ent else {"id": nid}), "rel": r["rel_type"], "depth": d + 1})
            frontier = nxt
        return out

    def types(self) -> list[dict]:
        """Distinct entity types in this project, with counts (most first)."""
        print("[aba.types]", flush=True)
        return self._rows(
            "SELECT type, COUNT(*) AS n FROM entities "
            "WHERE status != 'archived' GROUP BY type ORDER BY n DESC", []
        )

    # -- write verbs (Phase 2): emit INTENTS, executed backend-side post-run ----
    # The kernel never mutates the graph directly (no actor/provenance/SSE context
    # here, and two processes must not write one SQLite). Instead each write appends
    # an intent to $WORK_DIR/.aba_intents.jsonl; the backend's harvest_intents()
    # executes them after the run with full context. create() returns a LOCAL REF
    # ("aba:new:N") you can pass to relate() — refs resolve to real ids at harvest.
    def _emit(self, intent: dict) -> None:
        wd = os.environ.get("WORK_DIR") or os.getcwd()
        with open(os.path.join(wd, ".aba_intents.jsonl"), "a") as f:
            f.write(json.dumps(intent) + "\n")

    def create(self, type: str, title: str, **fields: Any) -> str:  # noqa: A002
        """Create a new entity (executed backend-side after this run, with
        provenance + actor stamped there). Returns a local ref for relate().
        Refuses lifecycle-managed types — use their dedicated verb instead."""
        lv = self._lifecycle_verbs.get(type)
        if lv:
            raise ValueError(
                f"{type!r} must be created with aba.{lv}(...), not aba.create — it needs "
                f"lifecycle wiring (evidence/interpretation/file adoption). See aba.help()."
            )
        ref = f"aba:new:{self._n}"; self._n += 1
        print(f"[aba.create] type={type!r} title={title!r} -> {ref}", flush=True)
        self._emit({"verb": "create", "ref": ref, "type": type, "title": title, "fields": fields})
        return ref

    def relate(self, source: str, rel: str, target: str) -> None:
        """Add a typed edge `source -rel-> target`. Args are entity ids or local
        refs returned by create() (resolved at harvest)."""
        print(f"[aba.relate] {source} -{rel}-> {target}", flush=True)
        self._emit({"verb": "relate", "source": source, "rel": rel, "target": target})

    def update(self, entity_id: str, **fields: Any) -> None:
        """Update fields (title/notes/tags/status/metadata) on an entity."""
        print(f"[aba.update] {entity_id} {list(fields)}", flush=True)
        self._emit({"verb": "update", "id": entity_id, "fields": fields})

    def emit_intent(self, verb: str, **fields: Any) -> str:
        """Emit a CONTENT-provided write intent (a verb core doesn't know — dispatched
        backend-side by a content-registered handler). Returns a local ref for chaining.
        Content packs attach domain verbs (e.g. aba.promote) onto this — the kernel-side
        equivalent of the core/services seam, so core names no domain concept."""
        ref = f"aba:new:{self._n}"; self._n += 1
        print(f"[aba.{verb}] -> {ref}", flush=True)
        self._emit({"verb": verb, "ref": ref, **fields})
        return ref

    # -- discovery (Phase 4) ---------------------------------------------------
    def help(self) -> str:
        """Print the aba verb reference. Reads: find/get/types/exists. Writes
        (deferred to end-of-run, provenance-stamped): create/relate/update.
        Use ops(type) for a type's required fields + allowed edges."""
        txt = (
            "aba — the project's entity graph, in this kernel.\n"
            "READS (return now):\n"
            "  aba.find(type=None, status=None, contains=None, limit=50) -> [rows]\n"
            "  aba.get(id, fields=None) -> row | None\n"
            "  aba.types() -> [{type, n}]   aba.exists(**predicates) -> bool\n"
            "  aba.provenance(id, direction='up'|'down', depth=8) -> lineage rows\n"
            "WRITES (applied after this run, with provenance + your actor):\n"
            "  aba.create(type, title, **fields) -> local ref (use in relate/update)\n"
            "  aba.relate(source, rel, target)   aba.update(id, **fields)\n"
            "  aba.ops(type) -> what a type needs (required fields, allowed edges)\n"
            "Tip: find() returns a LIST in one call — don't loop it."
        )
        if self._extra_help:
            txt += "\n" + self._extra_help
        print(txt, flush=True)
        return txt

    def ops(self, type: Optional[str] = None):  # noqa: A002
        """Registry-driven affordances. ops() lists entity types; ops(type)
        returns {required, optional, edges_out} for that type — so create()
        and relate() are self-describing. A new entity type appears here with
        no code change (registry-generated)."""
        reg = os.environ.get("ABA_TYPE_REGISTRY")
        if not reg:
            return {"error": "type registry unavailable in this kernel"}
        try:
            reg = json.loads(reg)
        except Exception:
            return {"error": "type registry malformed"}
        if type is None:
            print(f"[aba.ops] types: {sorted(reg)}", flush=True)
            return sorted(reg)
        print(f"[aba.ops] {type}", flush=True)
        return reg.get(type, {"error": f"unknown type {type!r}; known: {sorted(reg)}"})

    def __repr__(self) -> str:
        return ("<aba: reads .find/.get/.types/.exists; writes .create/.relate/.update; "
                "discovery .help()/.ops(type)>")
