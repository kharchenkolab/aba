"""One provenance-EVIDENCE view per entity, assembled from all three layers.

`docs/arch/provenance.md` keeps three co-located systems: descriptive
(`derivation`+`actor`), reproducible (the exec-record sidecar), and lineage
(`entity_edges`). The UI, the HTTP endpoint, and the agent all want the SAME
legible answer to "how was this made?" — so we assemble it once here, pairing each
piece of evidence with its version/identity:

    method       — code / command / pipeline  + code_hash, recipe, engine
    inputs       — datasets / files / refs     + content id / path fingerprint
    environment  — interpreter + packages      + language version, env_fingerprint
    attribution  — who + when                  + duration, status, seed
    lineage      — up/down neighbours           + edge label
    reproducibility — is there an exec record to re-run / revise

Never raises: every sub-block degrades to empty/None so a card render or an agent
turn is never blocked by a missing sidecar (best-effort, per the ~95% split).
"""
from __future__ import annotations

from core.graph.entities import get_entity
from core.graph import exec_records as _er
from core.graph.edges import edges_from
from core.graph.provenance import neighborhood, promotion_record

# Packages worth showing first in a trimmed environment summary (domain-notable),
# else we fall back to the first few alphabetically.
_KEY_PKGS = (
    "scanpy", "anndata", "scvi-tools", "scvi", "leidenalg", "igraph", "umap-learn",
    "seurat", "signac", "harmony", "scikit-learn", "sklearn", "numpy", "pandas",
    "scipy", "torch", "tensorflow", "matplotlib", "deseq2", "edger", "limma",
)
# Rels that reach a producing exec when an entity has no exec_id of its own.
_PRODUCER_EDGES = ("includes", "wasDerivedFrom", "wasGeneratedBy", "produced_by")


def _producing_execs(entity: dict, *, limit: int = 12) -> list[dict]:
    """The exec record(s) that produced `entity`, best-effort:

      - a figure/table/cell carries `exec_id` directly;
      - an analysis (Run) aggregates every exec in the run;
      - a Result/Finding resolves through its producing edges to member
        figures/tables (which carry exec_id) or to a Run.
    """
    execs: list[dict] = []
    seen: set[str] = set()

    def _add(exec_id: str | None) -> None:
        if not exec_id or exec_id in seen or len(execs) >= limit:
            return
        seen.add(exec_id)
        try:
            rec = _er.get(exec_id)
        except Exception:  # noqa: BLE001
            rec = None
        if rec:
            execs.append(rec)

    def _add_run(run_id: str | None) -> None:
        if not run_id:
            return
        try:
            for row in _er.list_by_run(run_id):
                _add(row.get("exec_id"))
        except Exception:  # noqa: BLE001
            pass

    if entity.get("exec_id"):
        _add(entity["exec_id"])
    if entity.get("type") == "analysis":
        _add_run(entity.get("id"))
    if not execs:
        for e in edges_from(entity.get("id")):
            if e.get("rel_type") not in _PRODUCER_EDGES:
                continue
            tgt = get_entity(e.get("target_id"))
            if not tgt:
                continue
            if tgt.get("exec_id"):
                _add(tgt["exec_id"])
            elif tgt.get("type") == "analysis":
                _add_run(tgt.get("id"))
    return execs


def _input_view(item: dict) -> dict:
    """Enrich a raw exec `inputs[]` item ({ref, kind, name?, path?}) with the
    referenced entity's title + a version/identity (content id or path fp)."""
    ref = item.get("ref")
    out: dict = {"ref": ref, "kind": item.get("kind") or "entity",
                 "name": item.get("name"), "path": item.get("path")}
    ent = None
    try:
        ent = get_entity(ref) if ref else None
    except Exception:  # noqa: BLE001
        ent = None
    if ent:
        out["kind"] = out["kind"] if out.get("kind") not in (None, "entity") else ent.get("type")
        out["name"] = out.get("name") or ent.get("title")
        out["title"] = ent.get("title")
        md = ent.get("metadata") or {}
        # Version/identity of the input, cheapest available: a registration
        # content hash if we have one, else the recorded path fingerprint.
        ver = (md.get("sha256") or md.get("content_hash") or md.get("version_lock")
               or (md.get("fingerprint") or {}).get("sha256"))
        if ver:
            out["version"] = str(ver)
        out["exists"] = True
    else:
        out["exists"] = bool(ref)
    return {k: v for k, v in out.items() if v is not None}


def _merge_inputs(execs: list[dict]) -> list[dict]:
    """Union `inputs[]` across producing execs, deduped by ref, enriched."""
    seen: set[str] = set()
    merged: list[dict] = []
    for rec in execs:
        for it in (rec.get("inputs") or []):
            ref = it.get("ref")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            merged.append(_input_view(it))
    return merged


def _env_drift(primary: dict) -> dict | None:
    """Cheap drift signal: has the environment MOVED since this ran? Compares the
    record's env_fingerprint against the most recent same-language run in the same
    thread — a pure sidecar read, no live interpreter probe on the hot path. Returns
    {changed, total} when it differs, else None. (The authoritative, exact drift is
    still computed live by `reproduce_from_exec` when the user actually re-runs.)"""
    ef = primary.get("env_fingerprint")
    tid = primary.get("thread_id")
    lang = primary.get("language")
    if not ef or not tid:
        return None
    try:
        rows = _er.list_by_thread(tid)
    except Exception:  # noqa: BLE001
        return None
    latest = None
    for row in reversed(rows):          # list_by_thread is started_at ASC → newest first
        try:
            rec = _er.get(row.get("exec_id"))
        except Exception:  # noqa: BLE001
            continue
        if rec and rec.get("language") == lang and rec.get("env_fingerprint"):
            latest = rec
            break
    if not latest or latest.get("env_fingerprint") == ef:
        return None                     # this IS the current env (or nothing to compare)
    a = primary.get("package_versions") or {}
    b = latest.get("package_versions") or {}
    if not a or not b:
        return {"changed": 0, "moved": True}   # fingerprints differ but no lists to diff
    names = set(a) | set(b)
    changed = sum(1 for k in names if a.get(k) != b.get(k))
    return {"changed": changed, "total": len(names)}


def _environment(primary: dict | None) -> dict:
    if not primary:
        return {}
    pkgs = primary.get("package_versions") or {}
    key = [{"name": n, "version": pkgs[n]} for n in _KEY_PKGS if n in pkgs]
    if not key and pkgs:
        key = [{"name": n, "version": pkgs[n]} for n in sorted(pkgs)[:6]]
    env: dict = {
        "language": primary.get("language"),
        "language_version": primary.get("language_version"),
        "env_fingerprint": primary.get("env_fingerprint"),
        "package_count": len(pkgs) or None,
        "key_packages": key,
        "images": (primary.get("env") or {}).get("per_process_images")
        if isinstance(primary.get("env"), dict) else None,
        # Whether the record can support a meaningful re-run/drift check.
        "backfilled": primary.get("source") == "backfill",
        "drift": _env_drift(primary),
    }
    return {k: v for k, v in env.items() if v not in (None, [], {})}


def _method(execs: list[dict]) -> dict:
    if not execs:
        return {}
    primary = execs[0]
    code = primary.get("code") or ""
    m: dict = {
        "kind": primary.get("kind"),
        "tool_name": primary.get("tool_name"),
        "executor": primary.get("executor"),
        "language": primary.get("language"),
        "code": code or None,
        "code_hash": primary.get("code_hash"),
        "code_lines": (code.count("\n") + 1) if code else None,
        "steps": len(execs) if len(execs) > 1 else None,
        "exec_id": primary.get("exec_id"),
        # cli/workflow producers
        "command": primary.get("command"),
        "engine": primary.get("engine"),
        "params": primary.get("params"),
        "recipe_id": primary.get("recipe_id"),
        "recipes": primary.get("recipes"),
    }
    return {k: v for k, v in m.items() if v is not None}


def _attribution(entity: dict, primary: dict | None) -> dict:
    a: dict = {
        "actor": entity.get("actor"),
        "created_at": entity.get("created_at"),
    }
    if primary:
        a.update({
            "started_at": primary.get("started_at"),
            "completed_at": primary.get("completed_at"),
            "wall_time_s": primary.get("wall_time_s"),
            "status": primary.get("status"),
            "seed": primary.get("seed"),
        })
    return {k: v for k, v in a.items() if v is not None}


def evidence(entity_id: str) -> dict | None:
    """Assemble the full provenance-evidence view for one entity. None if unknown."""
    entity = get_entity(entity_id)
    if not entity:
        return None

    execs = _producing_execs(entity)
    primary = execs[0] if execs else None

    nb = neighborhood(entity_id)          # {upstream, downstream}, nodes carry `rel`
    method = _method(execs)
    reproducibility = {
        "has_exec": bool(primary),
        "reproducible": bool(primary and primary.get("code")),
        "backfilled": bool(primary and primary.get("source") == "backfill"),
        # A revise/reproduce affordance only exists for exec-born artifacts today.
        "revisable": entity.get("type") in ("figure", "table"),
    }

    return {
        "entity": {"id": entity["id"], "type": entity["type"], "title": entity.get("title")},
        "method": method,
        "inputs": _merge_inputs(execs),
        "environment": _environment(primary),
        "attribution": _attribution(entity, primary),
        "lineage": nb,
        "promotion": promotion_record(entity),
        "reproducibility": reproducibility,
        # Back-compat: keep the flat keys the old panel consumed.
        "upstream": nb.get("upstream", []),
        "downstream": nb.get("downstream", []),
    }
