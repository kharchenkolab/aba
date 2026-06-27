"""Declarative entity-type registry — read-only.

Phase 4.2 of misc/modularity_audit.md / misc/phase4_entity_types.md.

A YAML per type lives in `content/<pack>/entity_types/<name>.yaml`. Bio
loads its directory at startup via `load_types(Path(...))`. After that
the registry serves:

  - get_type(name) -> EntityTypeSpec | None
  - list_types() / list_type_names()
  - valid_status_transition(type, from_, to) -> bool
  - valid_edge(src_type, tgt_type, rel) -> bool
  - card_builder_ref(type) / ui_panel_ref(type)
  - is_hidden(name) / hidden_types()

ENFORCEMENT IS NOT WIRED YET. The registry is queryable but nothing in
core/ uses it. Phase 4.3 migrates the 31 hardcoded type-string sites to
read through here; Phase 4.5 turns on schema + edge validation at the
write boundaries (warning-only first); Phase 4.4 enforces status
transitions through a generic state machine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityTypeSpec:
    """Per-type declaration. Top-level fields are required; nested
    dicts stay as-is (each consumer reads its own slice)."""
    name: str
    display: str
    icon: str
    hidden: bool
    schema: dict
    status_model: dict
    allowed_edges: dict
    focus: dict
    ui: dict
    creation: dict
    advisors: dict
    # Coarse-grained categorization (leaf, run, thread, claim, result,
    # finding, plan, …) — used by bio's files-tree composer to group
    # types by role. Infrastructure types (workspace, capability,
    # reference) leave this None. Phase 4.3 Pass 2.
    category: Optional[str] = None
    # Optional descriptive extra-state blocks (claim's confidence,
    # thread's lifecycle). Not engine-enforced today.
    confidence_model: Optional[dict] = None
    lifecycle_model: Optional[dict] = None
    # Declarative capability flags (is_artifact, is_run, artifact_group,
    # by_title_storage, …) so core/platform queries the registry instead of
    # naming bio types by literal (modularity_audit2 §Phase 3.3).
    capabilities: dict = field(default_factory=dict)

    def status_states(self) -> list[str]:
        return list(self.status_model.get("states") or [])

    def initial_status(self) -> str:
        return self.status_model.get("initial") or "active"

    def cap(self, name: str, default=None):
        """A declarative capability flag value (see `capabilities`)."""
        return self.capabilities.get(name, default)


_TYPES: dict[str, EntityTypeSpec] = {}


def _coerce(raw: dict, source: Path) -> EntityTypeSpec:
    """Build an EntityTypeSpec from a parsed YAML dict. Fills in
    optional blocks with empty defaults so consumers never need to
    test for None on a required key."""
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: missing/invalid 'name'")
    return EntityTypeSpec(
        name=name,
        display=raw.get("display") or name.capitalize(),
        icon=raw.get("icon") or "data",
        hidden=bool(raw.get("hidden") or False),
        schema=dict(raw.get("schema") or {"required": [], "optional": []}),
        status_model=dict(raw.get("status_model")
                          or {"initial": "active", "states": ["active"], "transitions": {}}),
        allowed_edges=dict(raw.get("allowed_edges") or {"out": [], "in": []}),
        focus=dict(raw.get("focus") or {}),
        ui=dict(raw.get("ui") or {}),
        creation=dict(raw.get("creation") or {}),
        advisors=dict(raw.get("advisors") or {}),
        category=raw.get("category"),
        confidence_model=(dict(raw["confidence_model"])
                          if "confidence_model" in raw else None),
        lifecycle_model=(dict(raw["lifecycle_model"])
                         if "lifecycle_model" in raw else None),
        capabilities=dict(raw.get("capabilities") or {}),
    )


def load_types(directory: Path) -> int:
    """Load every *.yaml in `directory` into the registry. Idempotent —
    re-loading replaces existing entries with the same `name`. Returns
    the number of types loaded. Logs (but does not raise on) per-file
    errors so a single bad YAML doesn't wedge the whole content pack."""
    if not directory.is_dir():
        log.warning("entity_types: directory not found: %s", directory)
        return 0
    n = 0
    for yml in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yml.read_text())
            if not isinstance(raw, dict):
                raise ValueError("YAML root must be a mapping")
            spec = _coerce(raw, yml)
            _TYPES[spec.name] = spec
            n += 1
        except Exception as exc:  # noqa: BLE001
            log.error("entity_types: failed to load %s: %s", yml, exc)
    return n


# --- Lookups ---


def get_type(name: str) -> Optional[EntityTypeSpec]:
    return _TYPES.get(name)


def list_types() -> list[EntityTypeSpec]:
    return list(_TYPES.values())


def list_type_names(*, include_hidden: bool = True) -> list[str]:
    return [t.name for t in _TYPES.values() if include_hidden or not t.hidden]


def types_in_category(category: str) -> set[str]:
    """All type names declaring `category: <category>` in their YAML.
    Empty set for an unknown category. Stable across calls (the registry
    is populated at startup)."""
    return {t.name for t in _TYPES.values() if t.category == category}


def types_with(cap: str, value=True) -> set[str]:
    """Type names whose capability `cap` equals `value` (default True) — so
    core/platform queries the registry instead of naming bio types (P3.3)."""
    return {t.name for t in _TYPES.values() if t.capabilities.get(cap) == value}


def artifact_groups() -> dict:
    """{output-group -> type name} for the types declaring an `artifact_group`
    capability (e.g. plots->figure, tables->table)."""
    return {t.capabilities["artifact_group"]: t.name
            for t in _TYPES.values() if t.capabilities.get("artifact_group")}


def by_title_storage(name: str) -> Optional[str]:
    """The by-title link layout for a type (artifact_file | run_dir | data_path),
    or None."""
    t = _TYPES.get(name)
    return t.capabilities.get("by_title_storage") if t else None


def is_hidden(name: str) -> bool:
    """True iff `name` is registered AND declared hidden. Unknown types
    return False — callers that want to treat unknowns as hidden can
    combine with `get_type(name) is None`."""
    t = _TYPES.get(name)
    return bool(t and t.hidden)


def hidden_types() -> tuple[str, ...]:
    """Tuple of all hidden type names — drop-in replacement for the
    HIDDEN_TYPES constant currently in core/graph/entities.py."""
    return tuple(t.name for t in _TYPES.values() if t.hidden)


# --- Validation predicates ---


def valid_status_transition(type_name: str, from_: str, to: str) -> bool:
    """True if the type's status_model allows `from_` → `to`.
    Returns False for unknown types or undeclared transitions."""
    t = _TYPES.get(type_name)
    if t is None:
        return False
    allowed = (t.status_model.get("transitions") or {}).get(from_) or []
    return to in allowed


def valid_edge(src_type: str, tgt_type: str, rel: str) -> bool:
    """True if both endpoints declare this rel on the correct side
    (source's `out` AND target's `in`). Unknown types yield False."""
    s = _TYPES.get(src_type)
    t = _TYPES.get(tgt_type)
    if s is None or t is None:
        return False
    return (rel in (s.allowed_edges.get("out") or [])
            and rel in (t.allowed_edges.get("in") or []))


# --- Pointers (forward references — resolved at use time) ---


def card_builder_ref(type_name: str) -> Optional[str]:
    t = _TYPES.get(type_name)
    return (t.focus.get("card_builder") if t else None)


def ui_panel_ref(type_name: str) -> Optional[str]:
    t = _TYPES.get(type_name)
    return (t.ui.get("panel") if t else None)


# --- Write-side validators (Phase 4.5) -----------------------------
# Warning-only initially. Callers should log the returned messages and
# proceed; after a week of real use, a follow-up commit may flip them
# to raise. Unknown types yield no warnings — legacy data + synthetic
# test types pass through untouched.


def check_create_fields(type_name: str, fields_present: dict) -> list[str]:
    """Return a list of human-readable warning messages — one per
    required field that's missing from `fields_present`. Empty list =
    OK or unknown type.

    `fields_present` is a dict of {field_name: value}; only truthy
    values count as present (None / "" / [] / {} are absent). This
    matches the natural call-site convention where callers pass
    {"title": title, "artifact_path": artifact_path, ...}.
    """
    t = _TYPES.get(type_name)
    if t is None:
        return []
    required = t.schema.get("required") or []
    return [
        f"{type_name}: required field '{f}' is missing or empty"
        for f in required
        if not fields_present.get(f)
    ]


def check_edge(src_type: str, tgt_type: str, rel: str) -> list[str]:
    """Warning messages for a disallowed edge. Empty list = OK or
    unknown types (one or both not in the registry)."""
    s = _TYPES.get(src_type)
    t = _TYPES.get(tgt_type)
    if s is None or t is None:
        return []
    src_out = s.allowed_edges.get("out") or []
    tgt_in = t.allowed_edges.get("in") or []
    msgs: list[str] = []
    if rel not in src_out:
        msgs.append(
            f"edge {src_type}--{rel}-->{tgt_type}: "
            f"rel '{rel}' not in {src_type}.allowed_edges.out ({src_out or '[]'})"
        )
    if rel not in tgt_in:
        msgs.append(
            f"edge {src_type}--{rel}-->{tgt_type}: "
            f"rel '{rel}' not in {tgt_type}.allowed_edges.in ({tgt_in or '[]'})"
        )
    return msgs
