"""Legacy backfill for the Phase-2 derivation/actor fields (modularity_audit2 §2D).

For entities created before provenance was recorded, infer a typed `derivation`
from what's already on the row / in the graph — never fabricate an origin:
  - exec_id present                  -> exec(exec_id)
  - has an outgoing "came-from" edge  -> derived_from([targets])
  - otherwise                         -> legacy()   (honest "origin unrecorded")
The historical `actor` is unknowable -> legacy.

Idempotent: only touches rows where `derivation IS NULL`, so it is safe to run on
every project open (a fully-backfilled project does no writes).
"""
from __future__ import annotations

import json

# Edges that mean "this entity came from <target>" (outgoing from the entity).
# Deliberately excludes relationship edges that are not derivation (supports,
# includes, used) — a bare entity with only those stays `legacy`, not invented.
_DERIVATION_EDGES = ("wasDerivedFrom", "wasGeneratedBy", "produced_by",
                     "wasRevisionOf", "variantOf")


def backfill_derivations() -> int:
    """Backfill `derivation` (+ a `legacy` actor) for entities missing them.
    Returns the number of rows backfilled."""
    from core.graph._schema import _conn
    from core.graph.derivation import (
        exec_derivation, derived_from, legacy, LEGACY_ACTOR,
    )

    placeholders = ",".join("?" * len(_DERIVATION_EDGES))
    n = 0
    with _conn() as c:
        rows = c.execute(
            "SELECT id, exec_id FROM entities WHERE derivation IS NULL"
        ).fetchall()
        for r in rows:
            eid, exec_id = r["id"], r["exec_id"]
            if exec_id:
                deriv = exec_derivation(exec_id)
            else:
                targets = [
                    t["target_id"] for t in c.execute(
                        f"SELECT target_id FROM entity_edges WHERE source_id=? "
                        f"AND rel_type IN ({placeholders})",
                        (eid, *_DERIVATION_EDGES),
                    ).fetchall()
                ]
                deriv = derived_from(targets) if targets else legacy()
            c.execute(
                "UPDATE entities SET derivation=?, actor=COALESCE(actor, ?) WHERE id=?",
                (json.dumps(deriv), LEGACY_ACTOR, eid),
            )
            n += 1
        c.commit()
    return n
