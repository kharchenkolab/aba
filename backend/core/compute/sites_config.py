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


def _site_yaml_sites() -> list[dict]:
    """Compute sites declared by the DEPLOYMENT, in site.yaml `compute.sites`.

    weft-sites.yaml lives in $ABA_HOME and is written by the installer, so it
    can only ever describe ONE user's machine. A shared deployment gives every
    user a fresh ABA_HOME, so there is no installer step and no file — which
    is exactly what happened on the OOD cluster (2026-08-25): site.yaml said
    `jobs.submitter: slurm`, no site was declared anywhere, and every
    background job silently ran inside the user's session container instead of
    on the scheduler. A deployment-wide fact belongs in the deployment's own
    config file.
    """
    try:
        from pathlib import Path as _P

        import yaml

        from core import config
        raw_path = (config.settings.site_config.get() or "").strip()
        if not raw_path:
            return []
        sp = _P(raw_path).expanduser()
        if not sp.is_file():
            return []
        doc = yaml.safe_load(sp.read_text()) or {}
    except Exception:  # noqa: BLE001 — no/broken site.yaml is not a boot error
        return []
    raw = ((doc.get("compute") or {}).get("sites")) or []
    return [_expand_site(e) for e in raw
            if isinstance(e, dict) and e.get("name")]


def _expand_site(entry: dict) -> dict:
    """Expand {user}/{home}/{group} through a site entry, at every depth.

    A shared deployment declares ONE site for ALL its users, so its paths have
    to be per-user templates — a literal weft root would put every user of the
    cluster in one workspace. These are the same placeholders the bundle
    scopes already accept, so the deployment author only learns one rule.
    Nested values (policy.storage.scratch) expand too: half-expanded config is
    worse than none, because it fails later and somewhere else.
    """
    import os
    from pathlib import Path as _P
    # $USER first, then the passwd entry — the same order core/bundle/
    # scope_resolver uses, so a site path and a bundle path can never expand
    # to two different users in one process.
    user = os.environ.get("USER")
    if not user:
        try:
            import pwd
            user = pwd.getpwuid(os.getuid()).pw_name
        except Exception:  # noqa: BLE001 — no passwd entry in some containers
            user = "unknown"
    home = str(_P.home())
    group = os.environ.get("ABA_GROUP") or ""

    def walk(v):
        if isinstance(v, str):
            return (v.replace("{user}", user)
                     .replace("{home}", home)
                     .replace("{group}", group))
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(dict(entry))


def list_declared_sites() -> list[dict]:
    """Every declared site: the deployment's (site.yaml) as the base, with the
    operator's $ABA_HOME/weft-sites.yaml overriding BY NAME — a site.yaml
    default must never lock out a local operator override."""
    try:
        doc = read_sites_config()
    except Exception:  # noqa: BLE001 — a broken file lists as empty; boot warns
        doc = {}
    local = [e for e in (doc.get("sites") or []) if isinstance(e, dict)]
    by_name = {e.get("name"): e for e in _site_yaml_sites()}
    by_name.update({e.get("name"): e for e in local})   # operator wins
    return list(by_name.values())


def aba_keys(name: str) -> dict:
    """The aba-side block for one site ({} when undeclared)."""
    for entry in list_declared_sites():
        if entry.get("name") == name:
            return dict(entry.get("aba") or {})
    return {}


def self_service() -> bool:
    """May users add/remove/reconfigure compute sites from the UI/agent?
    A registry setting (ABA_COMPUTE_SELF_SERVICE, default True) — a real
    deployment DECISION belongs in the central settings surface (validated,
    listed in settings-reference.md, deploy-injectable on OOD), not hidden
    inside the sites declaration file. Shared installs set it false: the
    Compute tab shows the deployment's machines read-only and the
    management API refuses with an actionable 403."""
    from core import config
    try:
        return bool(config.settings.compute_self_service.get())
    except Exception:  # noqa: BLE001 — a config hiccup must not lock the UI
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
