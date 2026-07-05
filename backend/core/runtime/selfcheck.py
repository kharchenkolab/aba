"""Startup self-check registry — a small, extensible home for platform health
checks that must be visible WITHOUT shell access (the loud-but-boot safety net;
a bare `print()` to backend.log only reaches an operator already tailing it).

Each check is a zero-arg callable returning ``{ok, severity, detail}`` (``name``
is filled in by the registry). ``run()`` executes every registered check once,
caches the results, and is read back by ``/api/health`` (``degraded`` + a
``warnings[]`` list) and ``/api/admin/selfcheck`` (the diagnostics drawer).

Deliberately generic: the ENVS_DIR-shared check is the first tenant, but the GPU
torch base, base-env integrity, etc. can register here too so every future boot
check reports through one surface instead of a scattered `print`.

Platform-only (``core``): checks may import ``core.*`` but never ``content.*``
(the plane seam). This module itself imports nothing heavy, so it is safe to
import from anywhere without cycles.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

# Severity rank — used to compute the "worst" outstanding warning for a summary.
_SEV_ORDER = {"info": 0, "warning": 1, "high": 2, "critical": 3}

# Registered checks (name -> fn) and the last run's cached results.
_checks: dict[str, Callable[[], dict]] = {}
_results: list[dict] = []
_ran_at: Optional[float] = None


def register(name: str, fn: Callable[[], dict]) -> None:
    """Register (or replace, by name) a self-check. ``fn()`` returns a dict with
    ``ok`` (bool), ``severity`` ('info'|'warning'|'high'|'critical'), and
    ``detail`` (str). Idempotent by name so re-registration on a reload is safe."""
    _checks[name] = fn


def run() -> list[dict]:
    """Run all registered checks, cache, and return the results. A check that
    raises becomes an ``ok=False``/``critical`` record — a broken check is itself
    a signal, never a silent skip."""
    global _results, _ran_at
    out: list[dict] = []
    for name, fn in _checks.items():
        try:
            r = fn() or {}
            rec = {
                "name": name,
                "ok": bool(r.get("ok", True)),
                "severity": str(r.get("severity", "warning")),
                "detail": str(r.get("detail", "")),
            }
        except Exception as e:  # noqa: BLE001 — a raising check must not crash startup
            rec = {"name": name, "ok": False, "severity": "critical",
                   "detail": f"self-check {name!r} raised: {e!r}"}
        out.append(rec)
    _results = out
    _ran_at = time.time()
    return out


def last_results() -> list[dict]:
    """The cached results of the most recent ``run()`` (empty before first run)."""
    return list(_results)


def warnings() -> list[dict]:
    """The subset of the last run that failed (``ok=False``)."""
    return [r for r in _results if not r.get("ok")]


def degraded() -> bool:
    """True iff any check in the last run failed."""
    return any(not r.get("ok") for r in _results)


def worst_severity() -> Optional[str]:
    """Highest severity among outstanding warnings, or None if healthy."""
    w = warnings()
    if not w:
        return None
    return max((r.get("severity", "warning") for r in w),
               key=lambda s: _SEV_ORDER.get(s, 0))


def summary() -> dict:
    """Compact view for health/admin surfaces."""
    return {
        "degraded": degraded(),
        "worst": worst_severity(),
        "ran_at": _ran_at,
        "warnings": warnings(),
        "checks": last_results(),
    }
