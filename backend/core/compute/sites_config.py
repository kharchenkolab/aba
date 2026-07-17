"""weft-sites.yaml — read/merge/write (misc/compute_settings.md §3b, §7).

weft's sqlite is the runtime truth for sites; this YAML is the *declarative
bootstrap* an install or the Settings→Compute tab writes. It survives a fresh
clone / OOD redeploy (adapter re-registers missing sites at configure()) and
is the home of the aba-side keys weft has no schema for:

    sites:
      - name: vbc
        kind: slurm
        config: { host: login.vbc.ac.at, root: /scratch/me/.weft, ... }
        aba:
          contract: shared-fs                      # shared-fs | detached
          use_for: [interactive, background, gpu]  # placement hints
          storage:
            - { path: /groups/lab, stable: true }  # long-term store

Writes are merge-by-name (unknown top-level and per-site keys preserved) and
atomic (tmp + os.replace) — a crash mid-write must never leave a truncated
file that would silently drop sites at the next boot.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from core.compute.adapter import sites_config_path

DEFAULT_USE_FOR = ["interactive", "background"]


def read_sites_config() -> dict:
    """The parsed document ({} when absent). Raises on unreadable YAML —
    callers that must tolerate corruption (boot) catch; the tab surfaces it."""
    path = sites_config_path()
    if not path.exists():
        return {}
    import yaml
    return yaml.safe_load(path.read_text()) or {}


def list_declared_sites() -> list[dict]:
    try:
        doc = read_sites_config()
    except Exception:  # noqa: BLE001 — a broken file lists as empty; boot warns
        return []
    return [e for e in (doc.get("sites") or []) if isinstance(e, dict)]


def aba_keys(name: str) -> dict:
    """The aba-side block for one site ({} when undeclared)."""
    for entry in list_declared_sites():
        if entry.get("name") == name:
            return dict(entry.get("aba") or {})
    return {}


def self_service() -> bool:
    """May users add/remove/reconfigure compute sites from the UI/agent?
    Shared installs (e.g. an OOD deployment whose slurm sites the admin
    declared in this file) set a top-level `self_service: false` — the
    Compute tab then shows the deployment's machines read-only, and the
    add/disconnect/edit surfaces disappear entirely. Defaults to True
    (personal installs). Admin-owned: it lives in the file the deployment
    already writes, not in a new env var."""
    try:
        return bool(read_sites_config().get("self_service", True))
    except Exception:  # noqa: BLE001 — unreadable file must not lock the UI
        return True


def _atomic_write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _dump(doc: dict) -> str:
    import yaml
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def upsert_site(name: str, kind: str, config: dict,
                aba: Optional[dict] = None) -> dict:
    """Add or update one site entry, preserving everything else in the file.
    `config` replaces the stored config wholesale (it is weft's whole truth
    for the site); `aba` merges key-by-key so a partial edit (say, use_for)
    keeps contract/storage. Returns the entry as written."""
    doc = read_sites_config()
    sites = [e for e in (doc.get("sites") or []) if isinstance(e, dict)]
    entry: dict[str, Any] = next(
        (e for e in sites if e.get("name") == name), None) or {"name": name}
    if entry not in sites:
        sites.append(entry)
    entry["kind"] = kind
    entry["config"] = dict(config)
    if aba is not None:
        merged = dict(entry.get("aba") or {})
        merged.update(aba)
        entry["aba"] = merged
    entry.setdefault("aba", {"contract": "shared-fs",
                             "use_for": list(DEFAULT_USE_FOR)})
    doc["sites"] = sites
    _atomic_write(sites_config_path(), _dump(doc))
    return entry


def remove_site(name: str) -> bool:
    """Drop a site entry (True when something was removed). The file keeps
    its other content; a missing file is a no-op."""
    path = sites_config_path()
    if not path.exists():
        return False
    doc = read_sites_config()
    sites = [e for e in (doc.get("sites") or []) if isinstance(e, dict)]
    kept = [e for e in sites if e.get("name") != name]
    if len(kept) == len(sites):
        return False
    doc["sites"] = kept
    _atomic_write(path, _dump(doc))
    return True
