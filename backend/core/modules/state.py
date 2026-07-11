"""Per-deployment module state — $ABA_HOME/modules.json (misc/modules.md).

Stores DESIRED intent (enabled/disabled, or unset → use the registry default) plus the
transient install status the reconciler writes (queued/installing/failed + progress +
error + version). ACTUAL readiness is PROBED live by the manager (never trusted from
here), so a hand-edited or stale file can't make a missing env read as ready.

Shape:
  {"modules": {"<id>": {"desired": "enabled"|"disabled"|null,
                        "status": "idle"|"queued"|"installing"|"failed",
                        "progress": str|null, "error": str|null, "version": str|null}}}
Robust: a missing/corrupt file reads as empty; writes are atomic (tmp + replace).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _aba_home() -> Path:
    return Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))


def _path() -> Path:
    return _aba_home() / "modules.json"


def load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {"modules": {}}
    try:
        d = json.loads(p.read_text())
        if isinstance(d, dict) and isinstance(d.get("modules"), dict):
            return d
    except Exception:  # noqa: BLE001 — a bad state file must never break a request
        pass
    return {"modules": {}}


def _save(d: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(p)


def _entry(d: dict[str, Any], module_id: str) -> dict[str, Any]:
    return d["modules"].setdefault(module_id, {})


def get_desired(module_id: str) -> str | None:
    """'enabled' | 'disabled' | None (unset → caller falls back to the registry default)."""
    v = load()["modules"].get(module_id, {}).get("desired")
    return v if v in ("enabled", "disabled") else None


def set_desired(module_id: str, desired: str | None) -> None:
    d = load()
    e = _entry(d, module_id)
    if desired in ("enabled", "disabled"):
        e["desired"] = desired
    else:
        e.pop("desired", None)
    _save(d)


def get_status(module_id: str) -> dict[str, Any]:
    e = load()["modules"].get(module_id, {})
    return {
        "status": e.get("status", "idle"),
        "progress": e.get("progress"),
        "error": e.get("error"),
        "version": e.get("version"),
    }


def set_status(module_id: str, status: str, *, progress: str | None = None,
               error: str | None = None, version: str | None = None) -> None:
    """Record the reconciler's transient install status. status ∈
    idle|queued|installing|failed. Passing progress/error/version overwrites them;
    they are otherwise left as-is so a status flip doesn't wipe context."""
    d = load()
    e = _entry(d, module_id)
    e["status"] = status
    if progress is not None:
        e["progress"] = progress
    if error is not None:
        e["error"] = error
    if version is not None:
        e["version"] = version
    if status == "idle":                       # a clean finish clears the transient fields
        e["error"] = None
        e["progress"] = None
    _save(d)
