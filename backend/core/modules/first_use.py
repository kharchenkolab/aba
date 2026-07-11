"""First-use gating (misc/modules.md). When a capability is requested whose module
isn't installed yet (e.g. a pagoda3 viewer, or an R analysis), map the request to a
module, kick off its install in the background, and hand back a friendly
"installing — retry shortly" note. Off-by-default modules (r-bio, viewer-pagoda3)
thus install on first demand without the user visiting Settings → Modules.
"""
from __future__ import annotations

from core.modules import manager, reconciler, registry
from core.modules.registry import ModuleSpec


def module_for_trigger(trigger: str) -> ModuleSpec | None:
    """Find the module whose first_use hints match `trigger` — an import name
    (`scanpy`), a file extension (`.lstar.zarr`), or a viewer id (`pagoda3`).
    Case-insensitive; exact, extension-suffix, or substring match."""
    t = (trigger or "").strip().lower()
    if not t:
        return None
    for spec in registry.all_modules():
        for fu in spec.first_use:
            f = fu.lower()
            if t == f or t == f.lstrip("."):
                return spec
            if f.startswith(".") and t.endswith(f):     # file extension
                return spec
            if f in t or t in f:                        # loose substring either way
                return spec
    return None


def ensure_for_trigger(trigger: str) -> dict | None:
    """If `trigger` maps to a not-yet-ready module, start its install and return a note
    dict {module, ready, note}. Returns None when no module matches or it's already
    ready (caller proceeds normally)."""
    spec = module_for_trigger(trigger)
    if spec is None or manager.actual_state(spec) == "ready":
        return None
    reconciler.ensure_module(spec.id)
    return {
        "module": spec.id,
        "ready": False,
        "note": (f"The {spec.title} is installing now (first use, ~{spec.est_time}). "
                 f"The rest of ABA keeps working — retry this once it's ready "
                 f"(watch Settings → Modules)."),
    }
