"""External-viewer launchers (viewers.md §3 'external' mode, §5 `open_external`).

A viewer declared with `mode: external` names an `open_external` launcher id.
A launcher turns a file node into a URL to open in a new tab/window — and,
when the file first needs a derived/converted artifact (e.g. .h5ad → a viewer's
native store), may also report a background *prepare* job the frontend can watch.

This registry is **domain-neutral** (no content/ imports — respects the core seam):
concrete launchers (pagoda3, cellxgene, …) live under content/ and register here
at import time, exactly as the viewer YAML declarations do.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class LaunchResult:
    """What a launcher hands back. `url` is where to point the new window
    (root-relative or absolute); `prepare_job_id` is set when a background
    conversion must finish before the data is ready; `set_local_storage` is
    origin-shared localStorage the host should seed before opening (values that
    start with '/' are OOD-base-prefixed by the frontend) — e.g. pointing
    pagoda3's copilot at ABA's proxy via `{'p3-agent-proxy': '/pagoda3-api'}`.
    `store_path` is the absolute path of the prepared on-disk store (when the
    launcher materializes one) — lets the download endpoint pack that same
    cached store into a `.lstar.zarr.zip` without re-deriving it."""
    url: str
    prepare_job_id: Optional[str] = None
    label: Optional[str] = None
    set_local_storage: Optional[dict] = None
    store_path: Optional[str] = None


# A launcher resolves (node, ctx) -> LaunchResult. `node` is a files-tree node
# (name/artifact_path/entity_id/…); `ctx` carries request context (project, path).
Launcher = Callable[[dict[str, Any], dict[str, Any]], LaunchResult]

_LAUNCHERS: dict[str, Launcher] = {}


def register_launcher(launcher_id: str, fn: Launcher) -> None:
    """Register (or replace) the launcher for an `open_external` id."""
    _LAUNCHERS[launcher_id] = fn


def get_launcher(launcher_id: str) -> Optional[Launcher]:
    return _LAUNCHERS.get(launcher_id)


def launch(launcher_id: str, node: dict[str, Any],
           ctx: Optional[dict[str, Any]] = None) -> LaunchResult:
    """Run the named launcher. Raises KeyError if none is registered
    (the endpoint maps that to a 501 — declared but not yet wired)."""
    fn = _LAUNCHERS.get(launcher_id)
    if fn is None:
        raise KeyError(f"no launcher registered for open_external={launcher_id!r}")
    return fn(node, ctx or {})
