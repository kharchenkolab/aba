"""Record role registration — this pack's entity types in Record terms.

The Record face (core/record/world.py) is domain-neutral; here the pack
declares which of its types play which role and how the claim ladder orders
into maturity rungs (index = rung; terminal states past the ladder's third
rung render as contested/refuted marks, a renderer concern)."""
from core.entity_types.registry import types_with
from core.record.world import register_record_roles

register_record_roles(
    {
        "question": "thread",
        # findings ARE claim-material ("a conclusion the scientist would
        # cite" — promotion.md); they reach their question through the
        # results they stand on (one-hop reference)
        "claim": ("claim", "finding"),
        "prose": "narrative",
        "note": "note",
    },
    maturity_order=("preliminary", "supported", "validated",
                    "contested", "refuted"),
    # the leftovers shelf sweeps whatever this pack flags as an artifact
    artifact_types=sorted(types_with("is_artifact")),
    # this pack keeps the ladder in metadata.confidence (claim.yaml: the
    # platform status column is lifecycle, not confidence)
    maturity_key="confidence",
    # narrative.yaml: metadata.text holds the prose body — the story stratum
    prose_body_key="text",
    # the full assertion: claims keep it in metadata.statement, findings in
    # metadata.text — the story drafts from statements, never truncated titles
    claim_statement_key=("statement", "text"),
)
