"""Record role registration — this pack's entity types in Record terms.

The Record face (core/record/world.py) is domain-neutral; here the pack
declares which of its types play which role and how the claim ladder orders
into maturity rungs (index = rung; terminal states past the ladder's third
rung render as contested/refuted marks, a renderer concern)."""
from core.record.world import register_record_roles

register_record_roles(
    {
        "question": "thread",
        "claim": "claim",
        "prose": "narrative",
        "note": "note",
    },
    maturity_order=("preliminary", "supported", "validated",
                    "contested", "refuted"),
)
