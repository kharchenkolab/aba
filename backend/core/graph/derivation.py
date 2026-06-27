"""Typed `derivation` + `actor` for entities (modularity_audit2 §Phase 2).

A *derivation* says HOW an entity came to be; an *actor* says WHO made it. Both are
**descriptive** provenance — the reproduction engine (`provenance.md`) keys off the
exec record's `exec_id`, never these. Stored as: `derivation` a small JSON object in
`entities.derivation`; `actor` a plain string in `entities.actor`.

Derivation kinds:
  exec(exec_id)        — produced by a recorded execution (figures/tables/cells)
  derived_from([ids])  — promoted/built from other entities (results, findings)
  imported(source)     — brought in from outside (datasets, references)
  manual()             — hand-authored in the app (narratives, notes, threads)
  legacy()             — created before provenance was recorded (backfill marker)

Actors:
  agent:<run_id>  — produced by an agent turn        (agent_actor)
  human:<uid>     — produced by a person             (human_actor; uid='local' until
                    real identity lands — the access cross-cut / OOD future)
  system          — produced by the platform itself  (SYSTEM_ACTOR)
  legacy          — unknown, pre-attribution          (LEGACY_ACTOR)
"""
from __future__ import annotations

from typing import Optional

VALID_KINDS = frozenset({"exec", "derived_from", "imported", "manual", "legacy"})


def exec_derivation(exec_id: str) -> dict:
    return {"kind": "exec", "exec_id": exec_id}


def derived_from(source_ids: list[str]) -> dict:
    return {"kind": "derived_from", "sources": list(source_ids)}


def imported(source: str) -> dict:
    return {"kind": "imported", "source": source}


def manual() -> dict:
    return {"kind": "manual"}


def legacy() -> dict:
    return {"kind": "legacy"}


SYSTEM_ACTOR = "system"
LEGACY_ACTOR = "legacy"


def agent_actor(run_id: str) -> str:
    return f"agent:{run_id}"


def human_actor(uid: str = "local") -> str:
    return f"human:{uid}"


def is_valid(derivation: Optional[dict]) -> bool:
    """A derivation is valid iff it carries a recognized `kind`."""
    return bool(derivation) and derivation.get("kind") in VALID_KINDS
