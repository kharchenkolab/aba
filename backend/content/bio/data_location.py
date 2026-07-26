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


def _norm_fs_path(p: "str | None") -> str:
    """Compare-normalize a filesystem path: collapse redundant separators and
    trailing slashes so a recorded home and a raw input that differ only by a
    trailing '/' read as the SAME file. Pure string work — never stats a disk,
    so it is safe for a remote path that doesn't exist on this machine."""
    import os
    if not p:
        return ""
    return os.path.normpath(str(p)).rstrip("/")


def _recorded_paths(e: dict) -> list:
    """Every filesystem path a dataset entity records its bytes under: the
    entity's artifact_path plus the by-reference metadata (ref_path and the
    durable home path). A remote by-reference dataset carries the same absolute
    path in all three."""
    md = e.get("metadata") or {}
    home = md.get("home") or {}
    return [e.get("artifact_path"), md.get("ref_path"), home.get("path")]


def entity_for_path(path: str) -> "dict | None":
    """Reverse-lookup: the LIVE dataset entity whose recorded home / reference /
    artifact path equals `path` exactly (trailing slashes normalized), else None.

    Answers a raw absolute path from recorded metadata alone — no remote
    inventory probe — so a byte-identical registered home (especially a remote
    by-reference dataset) resolves INSTANTLY and entity-backed, rather than via
    a ~10 s probe that misses and reports the file as absent. ABSOLUTE inputs
    only: run-registered datasets can record caller paths verbatim (possibly
    relative), and a relative input equal to such a string must not steal the
    resolution from a real files-tree node — the friction this lookup removes
    is absolute-path inputs anyway (equality with an absolute target implies
    the recorded path is absolute too, so one check covers both sides).
    Archived datasets are excluded (they are gone from the user's working view;
    resolving a raw path to one would silently resurrect it), so a path
    matching only an archived entity reads as no hit — the caller then falls
    through to its remote-path guidance."""
    import os
    from core.graph.entities import list_entities
    target = _norm_fs_path(path)
    if not target or not os.path.isabs(target):
        return None
    # Newest live match wins — same tie-break as _locate_project_run_output:
    # list_entities orders pinned-then-oldest, so without the reversal a stale
    # duplicate registration of the same path would shadow the current one.
    for e in reversed(list_entities(type_filter="dataset", include_archived=False)):
        if any(_norm_fs_path(c) == target for c in _recorded_paths(e)):
            return e
    return None


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


def plan_placement_note(plan_text: str) -> str | None:
    """Advisory for the present_plan ack: when a registered dataset lives on
    a remote site and the plan never mentions that site, say so — "remote
    compute follows the data" was prose; this is the check at the decision
    point (live 2026-07-26: a plan targeted local while its only input lived
    remote, and only the user's question surfaced it). Advisory, never a
    block: plans may legitimately mirror first or work on derived local
    data — the note asks for the placement to be DELIBERATE, not different.
    Mirrored datasets don't trigger (either copy works)."""
    import re
    from core.graph.entities import list_entities
    remote: dict[str, list[str]] = {}
    for e in list_entities(type_filter="dataset", include_archived=False):
        loc = dataset_location(e)
        if loc["remote"] and not loc["mirrored"]:
            remote.setdefault(loc["site"], []).append(
                (e.get("title") or e.get("id") or "dataset").strip())
    if not remote:
        return None
    missing = {s: names for s, names in remote.items()
               if not re.search(rf"\b{re.escape(s)}\b", plan_text)}
    if not missing:
        return None
    frag = "; ".join(
        f"{', '.join(names[:3])}{' (+' + str(len(names) - 3) + ' more)' if len(names) > 3 else ''}"
        f" lives on {site}" for site, names in sorted(missing.items()))
    first_site = next(iter(sorted(missing)))
    return (f"PLACEMENT CHECK: {frag} — but no plan step mentions that site. "
            f"Remote compute follows the data: run those steps there "
            f"(site='{first_site}'), mirror the data locally first, or state "
            f"why running elsewhere is right.")


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
