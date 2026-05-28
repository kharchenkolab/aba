"""Capability catalog — the registry of *what can run* (capabilities.md).

Capabilities and references are first-class entities in the same store as
figures/results (capabilities.md §4.1); there is no separate registry object.
P0 provides the query/registration/proposal API with minimal bodies — the
catalog is unseeded until P1 (conda executor + seed entries) and the demand
loop (search → propose → approve) lands in P3. Freezing the API here means
P1-P3 fill in behavior without reshaping callers.
"""
from core.catalog.catalog import (
    CAPABILITY, REFERENCE,
    register_capability, list_capabilities, resolve_capability,
    propose_capability, approve_capability, capability_status,
    register_seed_provider,
)

__all__ = [
    "CAPABILITY", "REFERENCE",
    "register_capability", "list_capabilities", "resolve_capability",
    "propose_capability", "approve_capability", "capability_status",
    "register_seed_provider",
]
