"""Tool-presentation policy — the SINGLE source of truth for how each agent
prompt_mode presents the tool catalog to the model.

Two independent PROSE-only knobs. The calling CONTRACT of every tool (property
names, types, required, enum, default) is IDENTICAL across all modes and is never
touched here — only human-facing prose is tiered:

  - docstring   : 'full'    → the tool's whole @mcp.tool docstring
                  'summary' → first line only (via _compact_description), EXCEPT
                              priority tools, which always keep the full docstring
  - param_prose : 'keep'    → input_schema keeps `description` + `title`
                  'drop'    → strip `description`/`title` from input_schema
                              (the full text is recoverable on demand via
                              `describe_tool` — the escape hatch)

Why this module exists (prj placement-hardening 2026-07): tool presentation is a
cross-cutting input to EVERY agent decision. Before this, a single `if compact`
branch in list_tools + a budget concern about the small-model (lean) window could
silently strip schema prose from the PRODUCTION agent (grounded_guide/standard).
Centralizing the per-tier rules here means a change is scoped to one tier and
reviewable in one place — tuning `lean` can never degrade `standard`. See
misc/tool_presentation.md and tests/test_tool_presentation.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolPresentation:
    docstring: str = "full"       # "full" | "summary"
    param_prose: str = "keep"     # "keep" | "drop"


# mode (AgentSpec.prompt_mode) → presentation. The ONLY place tiers are defined.
#
#   full       — small/local dev + the default guide: everything.
#   standard   — grounded_guide (the production agent): a capable model on a large
#                window. It gets FULL param prose (placement guidance etc.); only the
#                docstring PREFIX is summarized, for token economy. It is NOT subject
#                to the lean window and must never be cut to satisfy it.
#   lean /      small local models (haiku / qwen) on a tight window. They MAY drop
#   lean_small  param prose — an ISOLATED decision that never touches `standard`.
#
# A tier flips to 'drop' only with (1) a budget reason and (2) a contract-invariant
# test. lean/lean_small: small local models on a ~40K window whose static catalog is
# already at/over half-window even with NO param descriptions — they drop param prose
# to fit and lean on `describe_tool` to recover it. standard/full: NOT window-bound —
# they KEEP full param prose (the placement guidance etc.). Dropping for lean is
# ISOLATED here and can never affect standard.
_POLICY: dict[str, ToolPresentation] = {
    "full":       ToolPresentation(docstring="full",    param_prose="keep"),
    "standard":   ToolPresentation(docstring="summary", param_prose="keep"),
    "lean":       ToolPresentation(docstring="summary", param_prose="drop"),
    "lean_small": ToolPresentation(docstring="summary", param_prose="drop"),
}


def presentation_for(mode: str | None) -> ToolPresentation:
    """The presentation policy for an agent prompt_mode (unknown/None → 'full')."""
    return _POLICY.get(mode or "full", _POLICY["full"])


# Schema-node METADATA labels to strip (human prose). These are stripped only when
# they are keys of a schema NODE — NOT when they are property NAMES that happen to be
# spelled "title"/"description" (e.g. run_python's `title` param). That distinction is
# the whole point: dropping a property named "title" would break the calling contract.
_PROSE_KEYS = ("description", "title")
# Keys whose VALUE is a map of NAME → subschema — recurse into the values, keep names.
_NAME_MAP_KEYS = ("properties", "$defs", "definitions", "patternProperties")
# Keys whose value is a single subschema (or bool, for additionalProperties).
_SUBSCHEMA_KEYS = ("items", "additionalProperties", "not", "if", "then", "else",
                   "contains", "propertyNames")
# Keys whose value is a list of subschemas.
_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")


def strip_schema_prose(schema: Any) -> Any:
    """Return a copy of a JSON Schema with schema-node prose labels (`description`,
    `title`) removed, preserving the calling CONTRACT: property NAMES, types, enum,
    default, required. Prose is stripped only from schema nodes — never from a
    `properties`/`$defs` name-map's KEYS (a parameter literally named "title" stays).
    Invoked only when a tier's policy sets param_prose='drop'."""
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k in _PROSE_KEYS:
                continue
            if k in _NAME_MAP_KEYS and isinstance(v, dict):
                out[k] = {name: strip_schema_prose(sub) for name, sub in v.items()}
            elif k in _SUBSCHEMA_KEYS and isinstance(v, (dict, list)):
                out[k] = strip_schema_prose(v)
            elif k in _SCHEMA_LIST_KEYS and isinstance(v, list):
                out[k] = [strip_schema_prose(x) for x in v]
            else:
                out[k] = v          # scalars, enum lists, required list, $ref, … — as-is
        return out
    if isinstance(schema, list):
        return [strip_schema_prose(v) for v in schema]
    return schema
