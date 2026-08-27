"""Data-safety ledger + per-site holdings (misc/more_weft_ui.md §1/§2).

ONE query layer answering "is anything in this project going to disappear?"
and "what would disconnecting this machine orphan?" — consumed by the ledger
strip, the consequence cards, the storage meter, AND the Guide's
`data_safety_summary` tool, so chat and UI can never disagree.

Doctrine: a projection of recorded catalog state (retain rows, dataset
metadata, site declarations). NEVER probes sites or fingerprints on render
(freshness discipline: revalidation happens on use / on demand).

  One bounded exception: for a dataset registered on THIS machine (no recorded
  site), a single local `exists()` on its own artifact_path. That is not a site
  probe and not a fingerprint — no round trip, no hashing, no directory walk —
  and without it the ledger asserted "safe: bytes live in the workspace data
  folder" for a file that had been deleted out of band, which is the one claim
  the ledger exists to make honestly.

States (§1, exhaustive): safe | at_risk | changed | unknown.
- `at_risk` is a verdict about VALUED items (datasets, keeps) whose only copy
  sits on temporary storage — merely-temporary run files that nothing values
  are not ledger items (§8c reconciliation).
- v1 gap (documented): `unknown` requires recorded site health, which we do
  not persist yet — items on an unreachable site currently keep their last
  derived state. See docs/arch/compute-sites.md Known gaps.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.graph.kinds import DATASET

_log = logging.getLogger("aba.ledger")


def _durable_map() -> dict:
    """site name → durable declaration (True | '/path' | None). The local site
    is durable by construction (the adapter registers it with durable: True).

    Two sources, and the merge rule is about POSITIONS, not precedence: the
    deployment yaml wins only where it actually CARRIES a `durable` key. It
    used to win merely by naming the site, so a deployment that declares a
    cluster and says nothing about its storage — the normal case, since
    durability is a separate assertion — voted "not durable" and shadowed
    weft's own registration. Silence is not a declaration."""
    out: dict = {"local": True}
    stated: set = set()          # names the yaml took an actual position on
    try:
        from core.compute.sites_config import list_declared_sites
        for e in list_declared_sites():
            cfg = e.get("config") or e
            name = e.get("name") or cfg.get("name")
            if not name:
                continue
            if "durable" in cfg:
                out[name] = cfg.get("durable")
                stated.add(name)
            else:
                out.setdefault(name, None)
    except Exception as e:  # noqa: BLE001 — no sites file → local-only deployment
        _log.debug("ledger: no sites config (%s)", e)
    # Runtime-REGISTERED sites (weft's own store) carry the authoritative
    # durable declaration — the deployment yaml is only the installer's copy,
    # and a machine connected at runtime is invisible to it: its keeps and
    # dataset homes rendered at_risk despite a durable:True registration
    # (browser-study finding).
    try:
        from core.compute import adapter as _ad
        comp = _ad.get_compute()
        for s in comp.sync_call("sites_list"):
            name = s.get("name")
            # "local" is durable BY CONSTRUCTION (the adapter registers it so).
            # Never let a describe hiccup downgrade it: that single answer
            # would render every kept result in the workspace at risk.
            if not name or name == "local" or name in stated:
                continue
            try:
                desc = comp.sync_call("sites_describe", name)
                out[name] = (desc.get("storage") or {}).get("durable")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — substrate offline → yaml-only view
        pass
    return out


def _local_bytes_present(e: dict) -> bool:
    """Does a LOCAL dataset's artifact_path still exist on this filesystem?

    Only ever called for an entity with NO recorded site — a remote home has no
    meaningful local path and must never be stat'ed here (that would read as
    "gone" for every by-reference dataset). Directory stores count as present
    when the directory exists; we do not descend (cost, and an empty store is a
    different problem from a missing one). Unreadable/erroring → True: the
    ledger must not cry "missing" because of a permissions hiccup, since the
    failure mode we are closing is a FALSE safe, and a false alarm is its own
    kind of dishonesty."""
    ap = e.get("artifact_path")
    if not ap:
        return True
    try:
        import os
        p = str(ap)
        if p.startswith("/artifacts/"):
            # served-URL form: map to disk through the same mapper the viewers use
            from core.web.artifacts import _artifact_url_to_path
            d = _artifact_url_to_path(p)
            return bool(d and d.exists())
        if not os.path.isabs(p):
            return True          # relative/registry-relative: not ours to judge
        return os.path.exists(p)
    except Exception:  # noqa: BLE001 — never turn a probe error into a verdict
        return True


def _dataset_items(durable: dict) -> list[dict]:
    from core.graph.entities import list_entities
    items = []
    for e in list_entities(type_filter=DATASET, include_archived=False):
        md = e.get("metadata") or {}
        home = md.get("home") or {}
        site = home.get("site")
        bytes_ = ((md.get("descriptor") or {}).get("bytes")
                  or (md.get("fingerprint") or {}).get("bytes"))
        why = ""
        if md.get("source_changed") or md.get("drift"):
            state = "changed"
            why = "the data at its source changed since registration"
        elif md.get("source_missing"):
            state = "changed"
            why = "the data at its source is gone or unreachable"
        elif md.get("ref") or md.get("content_ref"):
            state = "safe"
            why = "content-addressed; re-obtainable from its origin"
        elif e.get("artifact_path") and not site and not _local_bytes_present(e):
            # A LOCAL dataset was called "safe" purely because it had an
            # artifact_path — the path was never checked. So a dataset whose
            # backing file had been deleted out of band (a stray rm, a cleaned
            # scratch dir) kept reporting safe, and the entity stayed active
            # pointing at nothing. Live 2026-07-26: exactly this, after a file
            # was removed with raw os.remove in a code block. A local stat is
            # cheap and it is the only way this claim can be true.
            state = "changed"
            why = "registered here, but its file is no longer on disk"
        elif e.get("artifact_path") and not site:
            state = "safe"
            why = "bytes live in the workspace data folder"
        elif site and durable.get(site):
            state = "safe"
            why = f"its data home on {site} is durable storage"
        elif site:
            state = "at_risk"
            why = f"referenced in place on {site}, which declares no durable storage"
        else:
            state = "safe"   # registered + local, no home = workspace-managed
            why = "managed in the workspace"
        items.append({"entity_id": e["id"], "kind": DATASET, "title": e.get("title"),
                      "state": state, "site": site, "bytes": bytes_, "why": why})
    return items


def _keep_items(durable: dict, site: Optional[str] = None) -> tuple[list[dict], bool]:
    """Retained runs (grouped by label = run id): kept-in-place on a durable
    site OR shipped to the workspace → safe; kept in place on a site whose
    durable declaration was revoked → at risk (the promise is broken).

    `in_place` is a PER-ROW fact and the verdict must stay per-row. One run
    routinely has several keeps on several sites — an interactive kernel on
    the workspace site, a Slurm job on the cluster — and this folded them
    with `in_place = any(rows)`, then asked which of the group's sites lacked
    a durable promise. So a kernel keep sitting safely on durable storage
    lent its in-place-ness to a cluster keep that had been COPIED off scratch
    into the workspace precisely because scratch is not durable, and the
    ledger flagged the copy-to-safety as the thing at risk (live 2026-08-27:
    two such runs, both false, on a workspace where nothing was at risk).
    A row that moved home cannot be at risk: its bytes are no longer there.

    Note the inverse never happens: weft REFUSES an in-place keep on a site
    with no durable declaration (retain.no_durable), so `in_place` implies
    the promise existed when the keep was made — which is why "no longer
    declares" is the honest phrasing for the risky case.

    Returns (items, ok). ok=False means the retention index was EXPECTED
    (substrate configured) but unreachable — the caller must surface a
    degraded state, never render the empty list as "all safe": during an
    outage the quiet ledger told the user their kept results were safe and
    the disconnect card showed a machine as empty (outage-honesty review).
    A weft-less fallback deployment (substrate never configured) stays a
    quiet ([], True) — nothing is being hidden there."""
    from core.compute import retention
    try:
        rows = retention.retained(site=site) or []
    except Exception as e:  # noqa: BLE001
        _log.debug("ledger: retained() unavailable (%s)", e)
        try:
            from core.compute import adapter as _ad
            expected = bool(_ad.status().get("ok"))
        except Exception:  # noqa: BLE001
            expected = False
        return [], not expected
    by_label: dict = {}
    for r in rows:
        if r.get("state") not in ("done", "pinned-pending", "queued", "inflight"):
            continue
        lbl = r.get("label") or r.get("target")
        g = by_label.setdefault(lbl, {"bytes": 0, "sites": set(),
                                      "in_place": set(), "risky": set(),
                                      "targets": []})
        s = r.get("site") or "local"
        g["bytes"] += r.get("bytes") or 0
        g["sites"].add(s)
        if not r.get("in_place"):
            continue                       # shipped home — its bytes left
        g["in_place"].add(s)
        if not durable.get(s):
            g["risky"].add(s)
            if r.get("target"):
                g["targets"].append(r["target"])
    items = []
    for lbl, g in by_label.items():
        risky = sorted(g["risky"])
        state = "at_risk" if risky else "safe"
        why = (f"kept in place on {'/'.join(risky)}, which no longer declares durable storage"
               if risky else "kept on durable storage")
        item = {"entity_id": lbl, "kind": "run_keeps", "title": None,
                "state": state, "site": "/".join(sorted(g["sites"])),
                "bytes": g["bytes"], "why": why,
                # which sites still hold these bytes IN PLACE — the set a
                # durable-off preview must reason about, whatever today's
                # declaration says
                "kept_in_place": sorted(g["in_place"])}
        if risky:
            item["remedy"] = {
                "action": "ship_home",
                "label": "Copy to the workspace",
                "targets": g["targets"],
                "note": (f"copies these files off {'/'.join(risky)} into the "
                         f"workspace, which is durable storage"),
            }
        items.append(item)
    return items, True


_UNATTRIBUTED = object()


def _project_run_titles() -> Optional[dict]:
    """run id → title for every run (analysis) in the ACTIVE project's graph,
    or None when the graph cannot be read.

    Keeps come from weft's store, which is one per WORKSPACE, not one per
    project — `retained()` has no project filter and never did. So the
    project rollup listed every kept run the user had ever made, in every
    project: a project holding one dataset reported 33 items, 32 of them
    other projects' runs, and flagged two of those for attention (live
    2026-08-27). The label a keep carries IS the run's entity id, so the
    active graph is the authority on which ones are ours."""
    try:
        from core.graph.entities import list_entities
        from core.graph.kinds import ANALYSIS
        return {e["id"]: e.get("title")
                for e in list_entities(type_filter=ANALYSIS,
                                       include_archived=True)}
    except Exception as e:  # noqa: BLE001 — unreadable graph → no attribution
        _log.debug("ledger: run titles unavailable (%s)", e)
        return None


def data_ledger(project_id: Optional[str] = None) -> dict:
    """The §1 rollup: every valued item in exactly one state, plus totals.
    `project_id` is accepted for the route shape; the graph is already scoped
    to the active project's DB — and now the KEEPS are too (see
    `_project_run_titles`; weft's retention index is workspace-wide).
    Keeps we cannot attribute to this project are not dropped in silence:
    `elsewhere` counts them, so a genuinely at-risk result in a project the
    user is not looking at still has somewhere to show up.
    `degraded: true` means the retention index was unreachable — kept-result
    rows may be MISSING from `items`, so the strip must not go quiet ("quiet
    means safe" is the UI contract)."""
    durable = _durable_map()
    keeps, keeps_ok = _keep_items(durable)
    mine = _project_run_titles()
    elsewhere = None
    if mine is None:
        # no attribution possible: show everything rather than an empty,
        # confidently-quiet list
        scoped = keeps
    else:
        scoped, foreign = [], []
        for k in keeps:
            title = mine.get(k["entity_id"], _UNATTRIBUTED)
            if title is _UNATTRIBUTED:
                foreign.append(k)
            else:
                scoped.append({**k, "title": title, "linkable": True})
        elsewhere = {"items": len(foreign),
                     "at_risk": sum(1 for k in foreign
                                    if k["state"] == "at_risk")}
    items = [{**d, "linkable": True} for d in _dataset_items(durable)] + scoped
    totals = {"items": len(items),
              "safe": sum(1 for i in items if i["state"] == "safe"),
              "at_risk": sum(1 for i in items if i["state"] == "at_risk"),
              "changed": sum(1 for i in items if i["state"] == "changed"),
              "unknown": sum(1 for i in items if i["state"] == "unknown")}
    # An item's `site` may be a COMPOSITE display string (a keep spanning
    # local+remote reads "local/mendel") — remote_sites is an enumeration of
    # real site NAMES, so decompose before collecting (the composite leaked
    # into the UI as a phantom third site: "(some on local/mendel, mendel)").
    sites = sorted({part
                    for i in items if i["site"]
                    for part in str(i["site"]).split("/")
                    if part and part != "local"})
    out = {"items": items, "totals": totals, "remote_sites": sites,
           "multi_site": bool(sites), "degraded": not keeps_ok}
    if elsewhere is not None:
        out["elsewhere"] = elsewhere
    if not keeps_ok:
        out["degraded_note"] = ("the retention index is unreachable — the "
                                "safety of kept results cannot be assessed "
                                "right now (they are missing from this list)")
    return out


def site_holdings(site: str) -> dict:
    """What lives ONLY on this machine (§2) — feeds every consequence card:
    kept results (count + bytes), dataset homes (referenced in place), and the
    at-risk-if-gone rollup a Disconnect preview needs."""
    durable = _durable_map()
    keeps, keeps_ok = _keep_items(durable, site=site)
    from core.graph.entities import list_entities
    homes = []
    for e in list_entities(type_filter=DATASET, include_archived=False):
        md = e.get("metadata") or {}
        home = md.get("home") or {}
        if home.get("site") == site:
            homes.append({"entity_id": e["id"], "title": e.get("title"),
                          "path": home.get("path")})
    kept_bytes = sum(k["bytes"] or 0 for k in keeps)
    # Un-declaring durable storage only endangers keeps whose bytes are STILL
    # HERE. A keep that was shipped to the workspace still carries this site
    # as its origin, so counting every row told the user that N results
    # "would become at risk" when some of them had already left the machine.
    in_place = [k for k in keeps if site in (k.get("kept_in_place") or [])]
    out = {"site": site,
           "kept_runs": len(keeps), "kept_bytes": kept_bytes,
           "kept_in_place": {
               "runs": len(in_place),
               "bytes": sum(k["bytes"] or 0 for k in in_place)},
           "dataset_homes": homes,
           "at_risk_if_gone": len(keeps) + len(homes)}
    if not keeps_ok:
        # a disconnect/durable-off card gated on kept_runs>0 showed NO
        # warning during an outage — the machine looked empty exactly when
        # it could not be assessed
        out["unknown"] = True
        out["note"] = ("compute substrate unreachable — what this machine "
                       "holds cannot be assessed right now; retry before "
                       "disconnecting")
    return out
