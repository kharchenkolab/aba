"""Data handles + execution context (capdat_impl.md §3, frozen interfaces).

These dataclasses are the contract every caller passes around. They are
deliberately over-provisioned for the one-VM case: `ExecContext.location`
already admits values like "hpc:short"/"remote:vendor" even though P0 only
ever sets "local", so the shape never changes as backends are added.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DataHandle:
    """An opaque reference to data. `version=None` floats to latest; `resolve`
    records the version actually delivered in the returned lock."""
    entity_id: str
    version: str | None = None


@dataclass
class ExecContext:
    """Where a run executes — drives both staging (data.md §7) and routing
    (capabilities.md §10). P0 only ever uses location="local"."""
    location: str = "local"                 # "local" | future: "hpc:<part>", "remote:<vendor>"
    project_id: str | None = None
    identity: str = "local"
    scope_chain: list[str] = field(default_factory=lambda: ["system"])


@dataclass
class StagedInput:
    """The result of resolving a handle: a locally-readable path plus the
    version lock to record in provenance."""
    local_path: str
    version_lock: str
