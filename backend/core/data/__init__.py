"""Data layer — handles, staging, and the project-tier store.

The membrane between the entity graph and the sandbox filesystem (data.md).
Skills/agents pass `DataHandle`s; `resolve` stages them to a local path;
`register` promotes a scratch file to a tracked artifact (store-by-reference);
`promote` elevates a project artifact to a shared reference.

P0 implements only the project tier: `resolve` returns the entity's existing
path (no copy, no staging), `register` records a path over the existing
content-addressed artifact store, `promote` is a scope flip. The reference
tier (content-addressed CAS, staging) lands in P4 — the signatures here do
not change when it does.
"""
from core.data.handles import DataHandle, ExecContext, StagedInput
from core.data.store import resolve, register, promote, version
from core.data.workspace import scratch_dir, gc_scratch
from core.data.refstore import (
    register_reference, find_reference, list_references, content_sha,
    get_reference, promote_reference,
)

__all__ = [
    "DataHandle", "ExecContext", "StagedInput",
    "resolve", "register", "promote", "version",
    "scratch_dir", "gc_scratch",
    "register_reference", "find_reference", "list_references", "content_sha",
    "get_reference", "promote_reference",
]
