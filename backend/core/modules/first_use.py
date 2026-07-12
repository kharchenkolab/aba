"""First-use gating (misc/modules.md). When a capability is requested whose module
isn't installed yet (e.g. a pagoda3 viewer, or an R analysis), map the request to a
module, kick off its install in the background, and hand back a friendly
"installing — retry shortly" note. Off-by-default modules (r-bio, viewer-pagoda3)
thus install on first demand without the user visiting Settings → Modules.
"""
from __future__ import annotations

from core.modules import manager, reconciler, registry, state
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


def gate_module(module_id: str) -> dict | None:
    """Gate a capability on its module's state. Returns None when the module is unknown
    or already ready (caller proceeds). Otherwise a note dict:
      • off          → {module, mode:'off', ready:False, can_enable:True, note} — NOT installed.
      • on/first_use → kicks the install and returns {module, mode, ready:False, note}.
    can_enable lets the UI / viewer page / chat offer a one-click 'Enable & install'."""
    spec = registry.get(module_id)
    if spec is None or manager.actual_state(spec) == "ready":
        return None
    m = manager.mode(spec)
    if m == "off":
        return {
            "module": spec.id, "mode": "off", "ready": False, "can_enable": True,
            "note": (f"The {spec.title} is turned OFF (Settings → Modules). "
                     f"Enable it (On or First use) to use this — it installs in ~{spec.est_time}."),
        }
    prior = manager.actual_state(spec)          # failed | installing | queued | not_installed
    reconciler.ensure_module(spec.id)           # kick (or retry a failed attempt); no-op if installing
    if prior == "failed":
        err = state.get_status(spec.id).get("error") or "see the module log"
        return {
            "module": spec.id, "mode": m, "ready": False, "failed": True, "error": err,
            "note": (f"The {spec.title} failed to install ({err}). Retrying now — if it fails again, "
                     f"the install log is at {reconciler._log_path(spec)}; I can inspect it and fix the cause."),
        }
    return {
        "module": spec.id, "mode": m, "ready": False, "can_enable": False,
        "note": (f"The {spec.title} is installing now (first use, ~{spec.est_time}). "
                 f"The rest of ABA keeps working — retry this once it's ready "
                 f"(watch Settings → Modules)."),
    }


def ensure_for_trigger(trigger: str) -> dict | None:
    """As gate_module, but resolves `trigger` (import name / ext / viewer id) to a
    module first. Returns None when nothing matches or it's ready."""
    spec = module_for_trigger(trigger)
    return gate_module(spec.id) if spec else None
