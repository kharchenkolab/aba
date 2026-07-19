"""Viewer registry (viewers.md §5).

Domain-neutral registration mechanism for file viewers. Each viewer
declares what it handles (entity types, MIME patterns, extensions) and
which mode it uses (canvas | modal | external). Content packs ship a
YAML manifest; lab / project / personal overlays layer on top with the
same loader pattern.

The frontend has the actual viewer components; this side just decides
which viewer (by id) applies to which node, and in what order.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# Three modes — see viewers.md §3.
MODES = {"canvas", "modal", "external"}


@dataclass(frozen=True)
class Viewer:
    id: str
    mode: str
    component: Optional[str] = None        # frontend component name (canvas/modal)
    open_external: Optional[str] = None    # bio launcher id (external)
    label: Optional[str] = None
    priority: int = 5
    entity_types: tuple[str, ...] = ()
    mime_patterns: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    applies_any: bool = False              # always-applicable (AI fallback)
    max_size_kb: Optional[int] = None
    requires_consent: bool = False


_VIEWERS: dict[str, Viewer] = {}
_DISABLED: set[str] = set()


def register_viewers_yaml(path: Path) -> None:
    """Load a viewers.yaml file and add / replace / disable entries."""
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text()) or {}
    for vid in data.get("disabled", []):
        _DISABLED.add(vid)
        _VIEWERS.pop(vid, None)
    for raw in data.get("viewers", []):
        v = _viewer_from_yaml(raw)
        if v is None:
            continue
        if v.id in _DISABLED:
            continue
        _VIEWERS[v.id] = v


def _viewer_from_yaml(raw: dict[str, Any]) -> Optional[Viewer]:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    mode = raw.get("mode")
    if mode not in MODES:
        return None
    return Viewer(
        id=str(raw["id"]),
        mode=mode,
        component=raw.get("component"),
        open_external=raw.get("open_external"),
        label=raw.get("label"),
        priority=int(raw.get("priority", 5)),
        entity_types=tuple(raw.get("entity_types") or ()),
        mime_patterns=tuple(raw.get("mime_patterns") or ()),
        extensions=tuple(raw.get("extensions") or ()),
        applies_any=bool(raw.get("applies") == "any"),
        max_size_kb=raw.get("max_size_kb"),
        requires_consent=bool(raw.get("requires_consent", False)),
    )


def list_viewers() -> list[Viewer]:
    return sorted({**_VIEWERS, **{v.id: v for v in viewers_from_catalog()}}.values(),
                  key=lambda v: (-v.priority, v.id))


def viewers_from_catalog() -> list[Viewer]:
    """Viewer-role capability entries projected as registry rows (weft
    rewrite #11): an EXTERNAL viewer is real software, so its registration is
    catalog DATA — a `role: viewer` capability with a declarative `viewer:`
    block naming what it opens and which registered launcher serves it —
    not a hand-maintained YAML row. (canvas/modal rows stay YAML: those are
    frontend components, not capabilities.) Queried live so the projection
    follows the active project's catalog; ids are namespaced `cap:<name>` so
    they can never collide with static rows. Best-effort: no catalog (bare
    embedder, unseeded store) → no rows, never an error."""
    try:
        from core.catalog import list_capabilities
        caps = list_capabilities(role="viewer")
    except Exception:  # noqa: BLE001
        return []
    out: list[Viewer] = []
    for cap in caps:
        if cap.get("status") not in (None, "published"):
            continue
        block = cap.get("viewer") or {}
        mode = block.get("mode") or "external"
        if mode not in MODES:
            continue
        out.append(Viewer(
            id=f"cap:{cap.get('name')}",
            mode=mode,
            component=block.get("component"),
            open_external=block.get("launcher"),
            label=block.get("label") or cap.get("name"),
            priority=int(block.get("priority", 5)),
            entity_types=tuple(block.get("entity_types") or ()),
            mime_patterns=tuple(block.get("mime_patterns") or ()),
            extensions=tuple(block.get("extensions") or ()),
            applies_any=bool(block.get("applies") == "any"),
            max_size_kb=block.get("max_size_kb"),
            requires_consent=bool(block.get("requires_consent", False)),
        ))
    return out


def viewers_for(node: dict[str, Any]) -> list[Viewer]:
    """Pick applicable viewers for a tree node (from
    content.bio.files.tree). Returns a list sorted by descending
    priority. The first entry is the default; the rest are alternates.
    Candidates = static rows (frontend components, YAML) + live catalog
    projections (role-tagged external viewers, #11)."""
    entity_type = (node.get("entity_type") or "").lower()
    artifact = node.get("artifact_path") or ""
    name = node.get("name") or ""
    size = node.get("size") or 0
    size_kb = (size + 1023) // 1024 if isinstance(size, int) else None
    ext = _ext_of(name or artifact)

    out: list[Viewer] = []
    candidates = {**_VIEWERS, **{v.id: v for v in viewers_from_catalog()}}
    for v in candidates.values():
        if v.id in _DISABLED:
            continue
        if v.max_size_kb and size_kb and size_kb > v.max_size_kb:
            continue
        if v.applies_any:
            out.append(v)
            continue
        match = False
        if entity_type and entity_type in v.entity_types:
            match = True
        # Suffix match (endswith) so multi-dot extensions like `.lstar.zarr`
        # work alongside single-dot ones (`.h5ad`, `.png`).
        name_l = (name or artifact).lower()
        if v.extensions and any(name_l.endswith(e.lower()) for e in v.extensions):
            match = True
        if v.mime_patterns and _mime_match(artifact, name, v.mime_patterns):
            match = True
        if match:
            out.append(v)
    out.sort(key=lambda v: (-v.priority, v.id))
    return out


def viewer_for(node: dict[str, Any]) -> Optional[Viewer]:
    """The default (highest-priority) viewer for a node, or None."""
    apps = viewers_for(node)
    return apps[0] if apps else None


# ---------- helpers ----------

_EXT_RE = re.compile(r"\.[A-Za-z0-9]+$")

def _ext_of(s: str) -> str:
    if not s:
        return ""
    m = _EXT_RE.search(s)
    return m.group(0).lower() if m else ""


_IMAGE_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}
_TEXT_BY_EXT = {
    ".txt": "text/plain", ".log": "text/plain", ".md": "text/markdown",
}
_APP_BY_EXT = {".pdf": "application/pdf", ".json": "application/json"}


def _mime_match(artifact: str, name: str, patterns: tuple[str, ...]) -> bool:
    """Cheap MIME inference from extension; matches against the viewer's
    declared patterns."""
    ext = _ext_of(artifact) or _ext_of(name)
    mime = (
        _IMAGE_BY_EXT.get(ext)
        or _TEXT_BY_EXT.get(ext)
        or _APP_BY_EXT.get(ext)
    )
    if not mime:
        return False
    for p in patterns:
        if p == mime:
            return True
        if p.endswith("/*") and mime.startswith(p[:-1]):
            return True
    return False


def to_wire(v: Viewer) -> dict[str, Any]:
    """Frontend representation of a viewer entry."""
    return {
        "id": v.id,
        "mode": v.mode,
        "component": v.component,
        "open_external": v.open_external,
        "label": v.label or v.id,
        "priority": v.priority,
        "requires_consent": v.requires_consent,
    }
