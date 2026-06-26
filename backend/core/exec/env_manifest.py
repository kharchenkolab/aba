"""Content-addressed env-manifest store (provenance.md §3.1 dedup).

The package-version list an exec ran with is ~8KB and is IDENTICAL across the
many runs that share an environment. Rather than inline it in every exec
record's JSON sidecar, we store one manifest per unique ``env_fingerprint``
under the runtime and have ``exec_records`` re-inflate it on read — so callers
see ``package_versions`` transparently while the bytes live exactly once
(shared across all projects, since the served env is shared).
"""
from __future__ import annotations
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def _path(fingerprint: str) -> Path:
    from core.config import RUNTIME_DIR
    d = RUNTIME_DIR / "env_manifests"
    d.mkdir(parents=True, exist_ok=True)
    return d / (fingerprint.replace(":", "_") + ".json")


def store(fingerprint: str, package_versions: dict, language_version: str = "") -> bool:
    """Persist the manifest under its fingerprint (idempotent). Returns False on
    an empty fingerprint/manifest or any write failure — the caller then keeps
    the manifest inline, so a store miss never loses data."""
    if not fingerprint or not package_versions:
        return False
    try:
        p = _path(fingerprint)
        if not p.exists():
            p.write_text(json.dumps(
                {"language_version": language_version,
                 "package_versions": package_versions},
                separators=(",", ":")), encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001
        _log.warning("env_manifest.store failed for %s: %s", fingerprint, e)
        return False


def load(fingerprint: str) -> dict:
    """Return {language_version, package_versions} for a fingerprint, or {}."""
    if not fingerprint:
        return {}
    try:
        p = _path(fingerprint)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        _log.warning("env_manifest.load failed for %s: %s", fingerprint, e)
    return {}
