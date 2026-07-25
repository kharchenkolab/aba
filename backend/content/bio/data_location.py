"""ONE owner for "where does this dataset live" on every naming surface.

The class this kills (location-surfacing census, 2026-07-26): a dataset's
home site IS recorded (`metadata.home.site`, set at registration) — but the
surfaces that NAME datasets (list_data_files, the kernel orientation block,
the [PROJECT STATE] sidebar) rendered title/path only. Silence reads as
"local": live, an agent with a remote-homed dataset probed the local disk
three times before inferring the site from an unrelated ambient line, and a
viewer link minted for a remote result died at click time. The paths
doctrine already says it: silence about location is a claim.

Contract for every consumer:
  - a LOCAL dataset gains ZERO noise (no suffix, site field says "local");
  - a REMOTE home is named wherever the dataset is named;
  - facts come from recorded metadata only — this module never stats disks
    or calls the substrate (rendering must stay cheap and outage-proof).

Guarded by tests/test_location_surfacing.py (the census: every naming
surface routes through here; new surfaces must enroll).
"""
from __future__ import annotations


def dataset_location(e: dict) -> dict:
    """Recorded location facts for a dataset entity.

    → {site, remote, by_reference, total_bytes, mirrored}
    `site` is always explicit ("local" when nothing says otherwise — an
    unset home on a workspace-managed dataset IS local); `total_bytes`
    comes from the captured descriptor/fingerprint, so it's available even
    when the controller holds no bytes (the local stat of a remote path
    used to render size as null — an unlabeled hint nobody reads)."""
    md = (e.get("metadata") or {})
    home = md.get("home") or {}
    site = home.get("site") or "local"
    desc = md.get("descriptor") or {}
    fp = md.get("fingerprint") or {}
    return {
        "site": site,
        "remote": site != "local",
        "by_reference": bool(md.get("by_reference")),
        "total_bytes": desc.get("total_bytes") or fp.get("total_bytes"),
        "mirrored": bool(md.get("local_mirror")),
    }


def location_suffix(e: dict) -> str:
    """The one-line rendering for naming surfaces: '' for local (zero
    noise), ' · on <site>' for a remote home, with the mirror noted when
    a local copy exists."""
    loc = dataset_location(e)
    if not loc["remote"]:
        return ""
    if loc["mirrored"]:
        return f" · on {loc['site']} (mirrored locally)"
    return f" · on {loc['site']}"


def remote_preview_answer(e: dict) -> dict | None:
    """Typed /preview answer for a remote-homed dataset with NO local bytes:
    `{kind:"remote", site, total_bytes}` — the card renders the site and the
    mirror lever instead of a silent empty preview. None when a normal
    preview should proceed (local dataset, mirrored copy, non-dataset)."""
    if (e or {}).get("type") != "dataset":
        return None
    loc = dataset_location(e)
    if not loc["remote"] or loc["mirrored"]:
        return None
    from pathlib import Path
    ap = e.get("artifact_path")
    if ap and Path(ap).exists():
        return None
    return {"kind": "remote", "site": loc["site"],
            "total_bytes": loc["total_bytes"]}


def remote_use_note(e: dict) -> str | None:
    """Actionable one-liner for tool results naming a remote dataset:
    what to DO about the location, not just the fact. None for local."""
    loc = dataset_location(e)
    if not loc["remote"]:
        return None
    if loc["mirrored"]:
        return (f"bytes live on {loc['site']}; a local mirror exists — "
                f"either works")
    return (f"bytes live on {loc['site']} — run compute there "
            f"(site='{loc['site']}'), or mirror the dataset locally first")
