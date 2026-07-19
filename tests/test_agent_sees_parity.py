"""Surface-parity guard for read_entity's default view.

read_entity(entity_id) with no explicit `fields` returns the type's
`focus.agent_sees` fields, each run through `_project`. If a field is
listed in agent_sees but has no projector, is not a real entity column,
and isn't a metadata key the type carries, `_project` returns None
SILENTLY — the agent goes blind to a state the UI renders (the exact
class of bug the surface-parity audit found: retention_alert,
run_outputs_summary, dataset drift, thread rollups).

This test is the class guard: for EVERY registered entity type, every
field in its agent_sees must RESOLVE via one of three routes —

  1. a projector in entity_ops._PROJECTORS, or
  2. a top-level entities-table column (fallback: `field in e`), or
  3. a metadata key the type is known to carry (fallback: `field in
     e['metadata']`) — enumerated in _METADATA_ALLOWLIST below with a
     reason, since metadata keys can't be enumerated statically.

Adding a field to any agent_sees with none of the above FAILS this
test — you must ship the projector (or, for a genuine metadata key, add
it to the allowlist with a comment) in the same change.

Run: ~/.aba/env/bin/python -m pytest tests/test_agent_sees_parity.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_agent_sees_parity_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "r.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, set_db_path  # noqa: E402

set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402  (loads entity_types into the registry)

from core.entity_types import get_type, list_types  # noqa: E402
from content.bio.mcp_servers.aba_core.tools.entity_ops import (  # noqa: E402
    _PROJECTORS,
    _TOP_LEVEL_COLUMNS,
)


# Top-level columns of the entities table — mirrors _row_to_entity in
# core/graph/entities.py. `_project` resolves any of these via `field in e`.
_ENTITY_COLUMNS = {
    "id", "type", "title", "status", "artifact_path", "producing_params",
    "parent_entity_id", "scenario_of", "metadata", "tags", "notes", "pinned",
    "display_path", "exec_id", "artifact_kind", "artifact_idx", "derivation",
    "actor", "deleted_at", "created_at", "updated_at",
}


# Metadata keys that agent_sees fields resolve to via read_entity's
# metadata fallback (`field in e['metadata']`). These CANNOT be
# enumerated from the schema, so they're allowlisted explicitly: a NEW
# agent_sees field with no projector/column trips the guard unless it is
# added here WITH a reason. Grandfathered entries (declared in a type's
# agent_sees before the parity audit, still awaiting a projector) are
# marked — they return None today and should get a projector, but they
# don't regress the surface further.
_METADATA_ALLOWLIST = {
    # analysis
    "run_state":              "analysis: metadata.run_state ('open'|'closed')",
    # thread
    "question":               "thread: metadata.question",
    "lifecycle":              "thread: metadata.lifecycle ('open'|'parked'|'concluded')",
    "open_questions":         "thread: metadata.open_questions[]",
    # capability
    "archetype":              "capability: metadata.archetype (propose_capability)",
    "install_recipe_summary": "capability: metadata rollup — grandfathered (no projector yet)",
    # plan
    "steps_summary":          "plan: metadata rollup — grandfathered (no projector yet)",
    "status_label":           "plan: metadata rollup — grandfathered (no projector yet)",
    # reference
    "assembly":               "reference: metadata.assembly",
    "version":                "reference: metadata.version",
    "source_url":             "reference: metadata.source_url",
    # workspace
    "project_snapshot":       "workspace: metadata rollup — grandfathered (no projector yet)",
}


def _resolves(field: str) -> bool:
    return (
        field in _PROJECTORS
        or field in _ENTITY_COLUMNS
        or field in _TOP_LEVEL_COLUMNS
        or field in _METADATA_ALLOWLIST
    )


def test_registry_loaded():
    """Sanity: the bio content pack registered its entity types, so the
    per-type loop below actually has types to check."""
    names = {t.name for t in list_types()}
    assert {"analysis", "dataset", "thread", "result", "claim"} <= names, names


def test_every_agent_sees_field_resolves():
    """The class guard: no agent_sees field silently projects to None."""
    unresolved: list[str] = []
    for spec in list_types():
        agent_sees = (spec.focus or {}).get("agent_sees") or []
        for field in agent_sees:
            if not _resolves(field):
                unresolved.append(f"{spec.name}.{field}")
    assert not unresolved, (
        "agent_sees fields with no projector / entity column / metadata "
        "allowlist entry — read_entity would silently return None for "
        "these (surface-parity blindness). Ship a projector in "
        "entity_ops._PROJECTORS, or (for a real metadata key) add the "
        "field to _METADATA_ALLOWLIST with a reason:\n  "
        + "\n  ".join(sorted(unresolved))
    )


def test_retention_alert_present_on_analysis():
    """Regression pin for the live finding that started the audit: a
    Run whose keepers couldn't be kept carries metadata.retention_alert,
    and the agent must see it. Both the agent_sees declaration and the
    projector must stay."""
    spec = get_type("analysis")
    assert spec is not None
    assert "retention_alert" in ((spec.focus or {}).get("agent_sees") or []), (
        "retention_alert dropped from analysis agent_sees — the agent "
        "goes blind to unkept-keeper alerts again."
    )
    assert "retention_alert" in _PROJECTORS, (
        "retention_alert projector dropped from _PROJECTORS."
    )


def test_audit_fixed_fields_have_projectors():
    """The four fields the parity audit added must all have projectors
    (not fall through to the metadata allowlist)."""
    for field in ("run_outputs_summary", "drift_state",
                  "pinned_count", "claim_count"):
        assert field in _PROJECTORS, f"{field} lost its projector"


def test_dropped_recent_outputs_stays_dropped():
    """recent_outputs was removed from thread agent_sees (ambiguous, no
    server-side reference implementation → always None). If it's
    re-added, it needs a real projector."""
    spec = get_type("thread")
    agent_sees = (spec.focus or {}).get("agent_sees") or []
    if "recent_outputs" in agent_sees:
        assert "recent_outputs" in _PROJECTORS, (
            "recent_outputs re-added to thread agent_sees without a "
            "projector — it would silently return None."
        )
