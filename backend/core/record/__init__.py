"""The Record — a document-first face over the entity graph (RECORD_DESIGN.md).

Phase 1 (read-only): `world.py` projects one project's graph into the World
shape the Record renderer consumes. Later phases add record-write proposal
kinds and advisor roles; nothing here writes.
"""
from core.record.world import assemble_world, register_record_roles  # noqa: F401
