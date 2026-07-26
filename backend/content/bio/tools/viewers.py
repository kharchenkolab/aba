"""open_viewer implementation — resolve an external viewer for an entity/file
and return a `/viewer-launch` URL Guide surfaces as a chat link.

Mirrors the /api/viewers/launch selection (viewers_for → external → pick), but
does NOT start the prepare job: it just hands back the launch URL. The
/viewer-launch page (opened when the user clicks the link) runs the prepare +
poll + redirect, so Guide's turn never blocks on conversion. See
misc/pagoda3_integration.md (surfacing, Tier 2).
"""
from __future__ import annotations

import os
from urllib.parse import urlencode


def _remote_open_note(site, size_bytes, *, mirror_lever) -> str:
    """One location pre-flight line for a REMOTE-homed source: what opening
    will cost, and the actionable lever. Wording is uniform across the entity
    and run-output branches; only `mirror_lever` (which lever actually exists
    for that source) differs. Facts come from recorded metadata only."""
    from core.data.datasets import FETCH_GUARDRAIL_BYTES
    if size_bytes and size_bytes > FETCH_GUARDRAIL_BYTES:
        return (f"source lives on {site} and is {size_bytes / 1e9:.1f} GB — OVER "
                f"the transfer gate, so opening from here will refuse. Work with "
                f"it on {site}, or reduce it there first.")
    mb = f" (~{size_bytes / 1e6:.0f} MB)" if size_bytes else ""
    return (f"source lives on {site}{mb} — opening fetches it to this machine "
            f"first; if that is refused, {mirror_lever}.")


def _remote_stream_note(site, *, mirror_lever) -> str:
    """Pre-flight for a REMOTE store that will STREAM its chunks on demand
    (range channel) — no whole-file fetch, so the transfer gate never applies.
    Keeps the mirror lever. Used only when streaming is actually available."""
    return (f"source lives on {site} — its chunks STREAM on demand as the viewer "
            f"reads them (no whole-file fetch, so the transfer gate doesn't apply); "
            f"{mirror_lever}.")


def _remote_stream_ready(run_id, name, *, entity=None) -> bool:
    """True when a remote source will STREAM its chunks. Two arms, tried
    cheapest-first:

    * REF arm — LAUNCHER-PARITY from RECORDED FACTS ONLY, NO remote round-trip:
      the ONE shared eligibility predicate (`ref_stream_facts` — the launcher's
      own gate set: store-suffix name, recorded data-plane `ref` OR a mintable
      durable home (the launcher mints the ref at click, in its async job —
      never here), remote + by-reference, recorded directory shape) plus the
      ref verb. Sharing the predicate is the point: a weaker gate here would
      promise streaming for a source the launcher then materializes into the
      transfer gate (the over-promising class the shared note exists to kill;
      the agreement matrix in tests/test_range_channel.py guards it). The ONE
      accepted divergence: a mint FAILURE at click degrades that launch to the
      fetch/bridge path after this note said streaming — mirror lever
      unaffected, so no hedging wording. This is what lets the entity branch
      promise streaming WITHOUT a producing run — and a by-ref entity never
      pays the run arm's resolve (the earlier cost concern).
    * RUN arm — the output resolves as a remote DIRECTORY store for its producing
      run: verb live AND `resolve_remote_store_stream` confirms it (a
      `locate_run_output` remote-tier pass plus an inventory read — a few ssh
      round-trips inside the link-mint call; the branch's earlier location facts
      don't carry kind/target, so they can't answer this without that resolve).

    Each verb-absent probe is cached and short-circuits with no round-trip, so on
    a substrate WITHOUT the verbs (today's deployments) this is free and returns
    False. Never raises — a note must never block the link."""
    try:
        from core.compute import retention
        # REF arm — the shared predicate over recorded facts; no round-trip.
        # (The launcher module is already imported: content.bio.viewers pulls
        # it in at package import, which open_viewer_impl guarantees.)
        if entity is not None:
            from content.bio.viewers.launchers.pagoda3 import ref_stream_facts
            if (ref_stream_facts(entity, name or "")
                    and retention.range_read_available(retention.DATA_RANGE_VERB)):
                return True
        # RUN arm — one inventory-backed resolve when the run verb is live.
        if not run_id or not name:
            return False
        if not retention.range_read_available():
            return False
        from content.bio.lifecycle.runs import resolve_remote_store_stream
        return bool(resolve_remote_store_stream(run_id, name))
    except Exception:  # noqa: BLE001
        return False


def _remote_note(site, size_bytes, *, mirror_lever, run_id, name,
                 entity=None) -> str:
    """THE one pre-flight decision for a REMOTE-homed source, shared by the
    entity and run-output branches: when the range channel will actually engage
    for this source (the ref arm — the shared `ref_stream_facts` predicate +
    the ref verb — OR the run arm — verb live + a remote directory store
    confirmed for the producing run), say chunks stream on demand — streaming
    makes the transfer gate irrelevant, so this wins over the over-gate refuse
    wording; otherwise the fetch / over-gate wording. Both branches MUST route
    through here (a branch calling `_remote_open_note` directly is
    streaming-blind — the class this closes)."""
    if _remote_stream_ready(run_id, name, entity=entity):
        return _remote_stream_note(site, mirror_lever=mirror_lever)
    return _remote_open_note(site, size_bytes, mirror_lever=mirror_lever)


def _entity_location_note(e: "dict | None") -> "str | None":
    """The entity-branch pre-flight note (remote source cost + mirror lever), or
    None for a local/mirrored/unknown source. The stream-or-fetch decision rides
    the shared `_remote_note`, with the producing run derived exactly as the
    launcher's entity resolution does (`run_id_for_entity`) and the name from
    the recorded path's basename (the same derivation as the dispatch node) —
    so the note promises what launch will actually do. The entity itself is
    passed through so the ref arm's shared predicate can read the SAME recorded
    facts the launcher will — a by-reference remote store promises streaming
    from recorded facts alone (no run, no round-trip — the ref arm). Never
    raises — an annotation must never block the link."""
    try:
        from content.bio.data_location import dataset_location
        loc = dataset_location(e or {})
        if not loc["remote"]:
            return None
        from content.bio.lifecycle.runs import run_id_for_entity
        md = (e or {}).get("metadata") or {}
        name_src = ((e or {}).get("artifact_path") or md.get("ref_path")
                    or (md.get("home") or {}).get("path") or "")
        return _remote_note(
            loc["site"], loc["total_bytes"],
            run_id=run_id_for_entity((e or {}).get("id")),
            name=(os.path.basename(name_src.rstrip("/")) if name_src else ""),
            entity=e,
            mirror_lever="mirror the dataset locally (its card has Mirror "
                         "locally), then reopen")
    except Exception:  # noqa: BLE001 — annotation must never block the link
        return None


def _shared_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _near_entity_hint(entity_id) -> list:
    """Up to 3 same-project entities whose id or title is a NEAR match for a
    missed id, each as {id, type, title} — cheap substring/shared-prefix, no new
    deps. (Titles are for the runtime message only.) Never raises."""
    try:
        from core.graph.entities import list_entities
        needle = (entity_id or "").strip().lower()
        if not needle:
            return []
        out = []
        for e in list_entities(include_archived=False):
            eid = (e.get("id") or "")
            el = eid.lower()
            title = (e.get("title") or "")
            near = (needle in el or (el and el in needle)
                    or _shared_prefix_len(el, needle) >= min(6, len(needle))
                    or (len(needle) >= 3 and needle in title.lower()))
            if near:
                out.append({"id": eid, "type": e.get("type"), "title": title})
            if len(out) >= 3:
                break
        return out
    except Exception:  # noqa: BLE001
        return []


def open_viewer_impl(params: dict, ctx: dict | None = None) -> dict:
    import content.bio  # noqa: F401 — ensure viewer + launcher registrations
    from core.viewers.registry import viewers_for
    from core.projects import current_project_id
    from core.graph.entities import get_entity

    entity_id = (params.get("entity_id") or "").strip() or None
    file_path = (params.get("file_path") or params.get("path") or "").strip() or None
    viewer_id = (params.get("viewer_id") or "").strip() or None

    # Fall back to the focused entity so "view this" works without an explicit id.
    if not entity_id and not file_path and ctx:
        entity_id = ctx.get("focus_entity_id") or None
    if not entity_id and not file_path:
        return {"ok": False, "error": "Provide entity_id or file_path (or focus an entity first)."}

    # F1 — REVERSE LOOKUP, before any remote probe: an absolute path that is
    # byte-identical to a registered dataset's recorded home resolves instantly
    # and entity-backed (pre-flight note + mirror lever), instead of via a
    # ~10 s inventory probe that misses and reports the file as absent.
    prefetched = None       # the matched entity dict — saves re-fetching it
    matched_path = None     # echoed as resolved_path on a reverse-lookup hit
    if not entity_id and file_path:
        from content.bio.data_location import entity_for_path
        match = entity_for_path(file_path)
        if match is not None:
            entity_id = match["id"]
            prefetched = match
            matched_path = os.path.normpath(file_path).rstrip("/")

    # Build a dispatch node. Match on the BASENAME (not the entity title) so
    # extension-based external viewers — pagoda3 (.h5ad / .lstar.zarr) — match:
    # viewers_for keys off `name or artifact_path`, and a title like "Processed
    # PBMC" wouldn't end in the file extension.
    link_path = matched_path  # canonical tree path used in the launch link (file case)
    note = None               # location pre-flight annotation (either branch)
    if entity_id:
        e = prefetched or get_entity(entity_id)
        if not e:
            near = _near_entity_hint(entity_id)
            hint = (" Did you mean: "
                    + "; ".join(f"{n['id']} ({n['type']}: {n['title']})" for n in near)
                    + "? Or call list_entities." if near
                    else " Call list_entities to see this project's entities.")
            return {"ok": False, "error": f"No entity {entity_id!r} in this project.{hint}"}
        artifact = e.get("artifact_path") or ""
        md = e.get("metadata") or {}
        # Viewer dispatch keys off a FILENAME extension: a by-reference entity
        # with no artifact_path (URL-import / home-only shape) still records a
        # reference path — derive the name from it, since the title lacks the
        # extension and would read as "no viewer applies".
        name_src = (artifact or md.get("ref_path")
                    or (md.get("home") or {}).get("path") or "")
        node = {
            "entity_id": e["id"],
            "entity_type": e.get("type"),
            "name": (os.path.basename(name_src.rstrip("/")) if name_src
                     else (e.get("title") or "")),
            "artifact_path": artifact,
            "size": None,
        }
        note = _entity_location_note(e)
    else:
        # Resolve file_path to a REAL files-tree node (a bare basename like
        # 'processed.h5ad' is fine — resolved by suffix/basename). Validate NOW so
        # we return a clear error to you instead of emitting a link that dies at
        # launch with "no file matching …".
        from content.bio.files.tree import build_files_tree, find_file_node, list_file_matches
        tree = build_files_tree(include_archived=False)
        n = find_file_node(tree, file_path)
        if n is not None:
            link_path = n.get("path")
            node = {
                "entity_id": n.get("entity_id"),
                "entity_type": n.get("entity_type"),
                "name": n.get("name") or os.path.basename(link_path or ""),
                "artifact_path": n.get("artifact_path") or link_path,
                "size": n.get("size"),
            }
        else:
            # Not in the entity-graph tree — a fresh weft Run output (e.g. a `.lstar.zarr` store
            # in the live kernel jobdir) that isn't a registered entity yet. Resolve it directly
            # from the Run's outputs so the user needn't data_register it first. The link carries
            # the same basename; the launch route re-resolves it the same way.
            from content.bio.lifecycle.runs import resolve_project_run_output_located
            located = resolve_project_run_output_located(file_path)
            if located is None:
                # F3 — informative miss. Keep the near-match candidate listing;
                # for an absolute path (no local resolve, no reverse-lookup hit,
                # probe miss) name the remote levers explicitly.
                cands = list_file_matches(tree, file_path)
                parts = []
                if cands:
                    parts.append("Matching files in this project: " + ", ".join(cands) + ".")
                if os.path.isabs(file_path):
                    parts.append("That looks like an absolute path — possibly a file on a "
                                 "remote site. Pass the dataset/artifact entity id instead "
                                 "(list_entities shows them), or register the file as an entity.")
                if not parts:
                    parts.append("No file with that name exists here — check the Files tab / your "
                                 "recent outputs, then pass its path or register it as a dataset "
                                 "and pass entity_id.")
                return {"ok": False,
                        "error": f"No file matching {file_path!r} in this project. " + " ".join(parts)}
            _rid, abs_path, _site, _size, _remote = located
            link_path = file_path
            node = {
                "entity_id": None,
                "entity_type": None,
                "name": os.path.basename(abs_path),
                "artifact_path": abs_path,
                "size": None,
            }
            if _remote:
                # F2 — same pre-flight on the path branch for a REMOTE run output.
                # No dataset card exists here, so the lever differs (register it),
                # but the wording — including the stream-or-fetch decision —
                # rides the same shared `_remote_note` as the entity branch.
                note = _remote_note(
                    _site, _size, run_id=_rid, name=node["name"],
                    mirror_lever=f"work with it on {_site}, or register it as a "
                                 f"dataset entity to enable a local mirror, "
                                 f"then reopen")

    ext = [v for v in viewers_for(node) if v.mode == "external" and v.open_external]
    if not ext:
        tgt = entity_id or link_path or file_path
        return {
            "ok": False,
            "error": (
                f"No external viewer applies to {tgt!r}. pagoda3 opens single-cell results "
                "saved as .h5ad or .lstar.zarr; anything else (figure, table, PDF, CSV) already "
                "opens inside ABA — don't offer a viewer link for those."
            ),
        }
    v = next((x for x in ext if x.id == viewer_id), None) if viewer_id else ext[0]
    if v is None:
        return {"ok": False, "error": f"No external viewer with id {viewer_id!r} applies here."}

    q = {"viewer": v.id, "project": current_project_id()}
    if v.label:
        q["label"] = v.label
    if entity_id:
        q["entity"] = entity_id
    else:
        q["path"] = link_path
    viewer_url = "/viewer-launch?" + urlencode(q)

    # LOCATION PRE-FLIGHT (surfacing census 2026-07-26): a link minted for a
    # REMOTE-homed source used to look identical to a local one and died at
    # click time ("lives on <site>" as a raw error card). The link stays valid —
    # the launcher fetches home under the transfer gate — but the result now SAYS
    # what opening will cost, and names the mirror lever when the gate would
    # refuse. `note` is set above by whichever branch resolved the source (entity
    # pre-flight or remote run-output), from recorded metadata only.
    label = v.label or v.id
    out_note = {"note": note} if note else {}
    return {
        "ok": True,
        "viewer_id": v.id,
        "label": label,
        "resolved_path": link_path,
        "viewer_url": viewer_url,
        **out_note,
        "_agent_hint": (
            f"Success: present viewer_url to the user as a markdown link — [{label}]({viewer_url}) — "
            "NOT the raw URL, and NOT with an emoji (the UI draws the button). It opens a new tab, "
            "shows a brief 'preparing…' screen while the store is built, then loads the viewer. "
            "(If a call returns ok:false, tell the user what the `error` says or retry with a "
            "corrected file — never hand out a link when ok:false.)"
        ),
    }
