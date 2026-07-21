"""Data-safety ledger + per-site holdings (misc/more_weft_ui.md §1/§2).

ONE query layer answering "is anything in this project going to disappear?"
and "what would disconnecting this machine orphan?" — consumed by the ledger
strip, the consequence cards, the storage meter, AND the Guide's
`data_safety_summary` tool, so chat and UI can never disagree.

Doctrine: a projection of recorded catalog state (retain rows, dataset
metadata, site declarations). NEVER probes sites or fingerprints on render
(freshness discipline: revalidation happens on use / on demand).

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
    is durable by construction (the adapter registers it with durable: True)."""
    out: dict = {"local": True}
    try:
        from core.compute.sites_config import list_declared_sites
        for e in list_declared_sites():
            cfg = e.get("config") or e
            out[e.get("name") or cfg.get("name")] = cfg.get("durable")
    except Exception as e:  # noqa: BLE001 — no sites file → local-only deployment
        _log.debug("ledger: no sites config (%s)", e)
    # Runtime-REGISTERED sites (weft's own store) carry the authoritative
    # durable declaration — the deployment yaml is only the installer's copy,
    # and a machine connected at runtime is invisible to it: its keeps and
    # dataset homes rendered at_risk despite a durable:True registration
    # (browser-study finding). Yaml wins where both name a site.
    try:
        from core.compute import adapter as _ad
        comp = _ad.get_compute()
        for s in comp.sync_call("sites_list"):
            name = s.get("name")
            if not name or name in out:
                continue
            try:
                desc = comp.sync_call("sites_describe", name)
                out[name] = (desc.get("storage") or {}).get("durable")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — substrate offline → yaml-only view
        pass
    return out


def _dataset_items(durable: dict) -> list[dict]:
    from core.graph.entities import list_entities
    items = []
    for e in list_entities(type_filter=DATASET, include_archived=False):
        md = e.get("metadata") or {}
        home = md.get("home") or md.get("weft_home") or {}
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
    site OR shipped to the workspace → safe; kept on a site whose durable
    declaration was revoked → at risk (the promise is broken).

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
        g = by_label.setdefault(lbl, {"bytes": 0, "sites": set(), "in_place": False})
        g["bytes"] += r.get("bytes") or 0
        g["sites"].add(r.get("site") or "local")
        g["in_place"] = g["in_place"] or bool(r.get("in_place"))
    items = []
    for lbl, g in by_label.items():
        risky = [s for s in g["sites"] if g["in_place"] and not durable.get(s)]
        state = "at_risk" if risky else "safe"
        why = (f"kept in place on {'/'.join(sorted(risky))}, which no longer declares durable storage"
               if risky else "kept on durable storage")
        items.append({"entity_id": lbl, "kind": "run_keeps", "title": None,
                      "state": state, "site": "/".join(sorted(g["sites"])),
                      "bytes": g["bytes"], "why": why})
    return items, True


def data_ledger(project_id: Optional[str] = None) -> dict:
    """The §1 rollup: every valued item in exactly one state, plus totals.
    `project_id` is accepted for the route shape; the graph is already scoped
    to the active project's DB. `degraded: true` means the retention index
    was unreachable — kept-result rows may be MISSING from `items`, so the
    strip must not go quiet ("quiet means safe" is the UI contract)."""
    durable = _durable_map()
    keeps, keeps_ok = _keep_items(durable)
    items = _dataset_items(durable) + keeps
    totals = {"items": len(items),
              "safe": sum(1 for i in items if i["state"] == "safe"),
              "at_risk": sum(1 for i in items if i["state"] == "at_risk"),
              "changed": sum(1 for i in items if i["state"] == "changed"),
              "unknown": sum(1 for i in items if i["state"] == "unknown")}
    sites = sorted({i["site"] for i in items if i["site"] and i["site"] != "local"})
    out = {"items": items, "totals": totals, "remote_sites": sites,
           "multi_site": bool(sites), "degraded": not keeps_ok}
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
        home = md.get("home") or md.get("weft_home") or {}
        if home.get("site") == site:
            homes.append({"entity_id": e["id"], "title": e.get("title"),
                          "path": home.get("path")})
    kept_bytes = sum(k["bytes"] or 0 for k in keeps)
    out = {"site": site,
           "kept_runs": len(keeps), "kept_bytes": kept_bytes,
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
