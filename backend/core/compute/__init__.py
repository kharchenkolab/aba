"""The Compute plane's port layer — aba's ONLY doorway to the weft substrate.

misc/weft_rewrite.md §3: three thin port Protocols (EnvPort / RunPort /
SitePort) over ONE `WeftAdapter` that owns the process-wide `Weft(workspace)`
instance. Nothing outside this package imports `weft`; planes reach compute
through these ports and the waist stays below everything (plane lint, W0.2).

Usage:
    from core import compute
    adapter = compute.get_compute()          # raises ComputeError if unavailable
    res = await adapter.env_ensure(spec)

Lifecycle: `configure()` at startup (best-effort — a missing weft package or
pixi binary records a degraded status that `status()` / the selfcheck surface;
it never blocks boot). `shutdown()` on app exit.
"""
from core.compute.errors import ComputeError
from core.compute.adapter import (
    WeftAdapter, configure, get_compute, shutdown, status, check_compute,
    weft_workspace, resolve_pixi,
)
from core.compute.ports import EnvPort, RunPort, SitePort

__all__ = [
    "ComputeError", "WeftAdapter", "EnvPort", "RunPort", "SitePort",
    "configure", "get_compute", "shutdown", "status", "check_compute",
    "weft_workspace", "resolve_pixi",
]
