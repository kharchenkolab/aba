"""Declarative entity-type registry (Phase 4 of misc/modularity_audit.md).

Content packs (today: content/bio/) declare each entity type as a YAML
file in their `entity_types/` directory. Platform code reads through
this registry — never by hardcoded string literal.

Pattern (mirrors core.hooks, core.prompts, core.manifest.assembler's
card-builder registry): content registers at startup; platform looks up.
"""
from core.entity_types.registry import (  # noqa: F401
    load_types, get_type, list_types, list_type_names,
    valid_status_transition, valid_edge,
    check_create_fields, check_edge,
    is_hidden, hidden_types,
    card_builder_ref, ui_panel_ref,
)
