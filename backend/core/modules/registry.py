"""Module registry — the catalog of capability packs (misc/modules.md).

DATA-DRIVEN: modules are declared as manifests (install/core/modules/*.yaml), loaded
here at first use. Adding or reconfiguring a module needs no change to this file — drop
a manifest (+ its install script) and it appears. Readiness probes + install/remove are
declared in the manifest (interpreted by manager.py / reconciler.py), so there's no
per-module Python.

Each module has one of three STATES: on (installed at boot), first_use (auto-installs
on first demand), off (never auto-installs; a request is refused with a nudge to enable).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

STATES = ("on", "first_use", "off")


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    title: str
    description: str
    size: str                      # human estimate, e.g. "~2 GB"
    est_time: str                  # human estimate, e.g. "3–5 min"
    default_state: str             # on | first_use | off (when unset in modules.json)
    env_target: str                # base-update | conda-tools | assets
    install_script: str            # ABSOLUTE path to the install script
    removable: bool                # can be uninstalled to reclaim disk
    order: int = 100               # display order in Settings → Modules
    first_use: tuple[str, ...] = field(default_factory=tuple)  # trigger hints (imports / exts / viewer ids)
    ready: dict = field(default_factory=dict)                  # declarative readiness probe (manager interprets)
    remove: dict = field(default_factory=dict)                 # declarative reclaim (reconciler interprets)


def _manifest_dirs() -> list[Path]:
    """Where module manifests live. Built-in dir today; bundle-scoped dirs slot in here
    later (misc/modules.md Phase 3) without touching callers."""
    here = Path(__file__).resolve()
    dirs = [here.parents[3] / "install" / "core" / "modules"]           # repo checkout
    dirs.append(Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))
                / "repo" / "aba" / "install" / "core" / "modules")      # deployed layout
    seen, out = set(), []
    for d in dirs:
        r = str(d.resolve()) if d.exists() else str(d)
        if r not in seen:
            seen.add(r); out.append(d)
    return out


def _spec_from_manifest(path: Path, data: dict) -> ModuleSpec:
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError(f"module manifest missing 'id': {path}")
    install = data.get("install") or ""
    # YAML 1.1 reads bare on/off/yes/no as booleans — tolerate that for default_state.
    _ds = data.get("default_state")
    _ds = {True: "on", False: "off"}.get(_ds, _ds)
    return ModuleSpec(
        id=str(data["id"]),
        title=str(data.get("title") or data["id"]),
        description=str(data.get("description") or "").strip(),
        size=str(data.get("size") or ""),
        est_time=str(data.get("est_time") or ""),
        default_state=(_ds if _ds in STATES else "first_use"),
        env_target=str(data.get("env_target") or ""),
        install_script=str((path.parent / install).resolve()) if install else "",
        removable=bool(data.get("removable", False)),
        order=int(data.get("order", 100)),
        first_use=tuple(str(x) for x in (data.get("first_use") or ())),
        ready=dict(data.get("ready") or {}),
        remove=dict(data.get("remove") or {}),
    )


def _load() -> tuple[ModuleSpec, ...]:
    import yaml
    by_id: dict[str, ModuleSpec] = {}
    for d in _manifest_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            try:
                spec = _spec_from_manifest(f, yaml.safe_load(f.read_text()) or {})
            except Exception as e:  # noqa: BLE001 — a bad manifest must not break the whole registry
                import sys
                print(f"[modules] skipping bad manifest {f}: {e}", file=sys.stderr)
                continue
            by_id.setdefault(spec.id, spec)   # first dir wins (repo before deployed copy)
    return tuple(sorted(by_id.values(), key=lambda m: (m.order, m.id)))


_CACHE: tuple[ModuleSpec, ...] | None = None


def all_modules() -> tuple[ModuleSpec, ...]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load()
    return _CACHE


def reload() -> None:
    """Drop the cache (tests / after a manifest sync)."""
    global _CACHE
    _CACHE = None


def get(module_id: str) -> ModuleSpec | None:
    return next((m for m in all_modules() if m.id == module_id), None)


def ids() -> tuple[str, ...]:
    return tuple(m.id for m in all_modules())
