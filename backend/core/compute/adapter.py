"""WeftAdapter — the ONE object behind all three compute ports.

Owns the process-wide embedded `Weft(workspace)` instance (weft is a
synchronous library over single-writer sqlite; its own pollers/drivers run on
daemon threads). Every port call runs on a small dedicated thread pool so the
event loop never blocks on a solve or a submission; weft's Store serializes
writes internally with an RLock, so a few concurrent threads are safe and a
long solve does not starve kernel polls.

Pass-through mechanics: any *public weft tool* (methods carrying the
`_weft_tool` marker) is exposed as an equivalently-named async method via
``__getattr__`` — the ports in ports.py document which calls belong to which
port. Error payloads (weft never raises across its boundary) become
`ComputeError`.

Lifecycle: `configure()` at startup — best-effort: a missing weft package /
pixi binary records a degraded status surfaced by `status()` + the
`compute_substrate` selfcheck, and any later `get_compute()` raises a
ComputeError naming the fix. It never blocks boot (W0 is wiring, not a
runtime dependency; W1 makes envs flow through here).
"""
from __future__ import annotations

import asyncio
import functools
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from core import config
from core.compute.errors import ComputeError, is_error_payload

_LOCAL_SITE = "local"


def run_sync(coro):
    """Run a port coroutine from a WORKER thread (tools run via
    run_in_executor; the one-shot run path is sync). Loud on the event-loop
    thread — blocking the loop on a solve/pack is never acceptable; use the
    async port there instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("run_sync is worker-thread-only: on the event loop, "
                       "await the port directly")


def weft_workspace() -> Path:
    """The deployment's weft workspace (holds .weft state + the local site
    root). One per deployment; per-project identity stays in the waist."""
    raw = config.settings.weft_workspace.get()
    return Path(raw) if raw else config.aba_home() / "weft"


def resolve_pixi() -> Optional[str]:
    """The pixi binary weft solves with: explicit setting → $PATH → the
    install-tree default. None when nowhere to be found (degraded)."""
    explicit = config.settings.pixi_bin.get()
    if explicit:
        return explicit
    found = shutil.which("pixi")
    if found:
        return found
    candidate = config.aba_home() / "tools" / "pixi" / "bin" / "pixi"
    return str(candidate) if candidate.exists() else None


class WeftAdapter:
    """Implements SitePort + EnvPort + RunPort (see ports.py) over one Weft."""

    def __init__(self, workspace: Path, pixi_bin: str):
        from weft.api import Weft   # the only weft import in aba
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="weft")
        self.workspace = workspace
        workspace.mkdir(parents=True, exist_ok=True)
        self._weft = Weft(workspace, pixi_bin=pixi_bin)
        self._ensure_local_site(pixi_bin)

    def _ensure_local_site(self, pixi_bin: str) -> None:
        registered = {s.get("name") for s in self._weft.sites_list()}
        if _LOCAL_SITE not in registered:
            r = self._weft.register_site(
                _LOCAL_SITE, "local",
                {"root": str(self.workspace / "site-local"),
                 "pixi_source": pixi_bin})
            if is_error_payload(r):
                raise ComputeError.from_payload(r)

    # -- pass-through machinery ------------------------------------------------

    async def _call(self, name: str, /, *args: Any, **kw: Any) -> Any:
        fn = getattr(self._weft, name)
        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(self._pool, functools.partial(fn, *args, **kw))
        if is_error_payload(out):
            raise ComputeError.from_payload(out)
        return out

    def __getattr__(self, name: str):
        # Fallback for weft tools not (yet) declared on a port Protocol —
        # anything else is a typo and must fail loudly here, not disappear
        # into weft internals. Dunder/private lookups fail fast (also guards
        # __init__-time recursion before self._weft exists).
        if name.startswith("_"):
            raise AttributeError(name)
        target = getattr(type(self._weft), name, None)
        if target is None or not getattr(target, "_weft_tool", False):
            raise AttributeError(f"WeftAdapter: {name!r} is not a weft tool")
        return functools.partial(self._call, name)

    def sync_call(self, name: str, /, *args: Any, **kw: Any) -> Any:
        """SYNCHRONOUS pass-through for FAST weft calls (store reads/writes:
        task_submit with a known env, task_status, task_cancel) from contexts
        that cannot await — e.g. the job submitters, which today run on the
        event-loop thread. NEVER use for solves/realizations (env_ensure on a
        fresh spec) — those block for minutes and belong on the async port.
        Same error conversion as the async path."""
        target = getattr(type(self._weft), name, None)
        if target is None or not getattr(target, "_weft_tool", False):
            raise AttributeError(f"WeftAdapter: {name!r} is not a weft tool")
        out = getattr(self._weft, name)(*args, **kw)
        if is_error_payload(out):
            raise ComputeError.from_payload(out)
        return out

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _install_port_methods() -> None:
    """Materialize every port-Protocol member as a real async pass-through
    method on WeftAdapter (same-named weft tool). Real methods — not just
    __getattr__ — so Python 3.12's getattr_static-based runtime Protocol
    checks see them, and so the port surface is greppable on the class.
    test_compute_ports.py asserts each name is a genuine weft tool, which
    turns a weft-side rename into a loud test failure instead of a runtime
    AttributeError."""
    from core.compute.ports import EnvPort, RunPort, SitePort

    def _make(name: str):
        async def method(self, *args: Any, **kw: Any) -> Any:
            return await self._call(name, *args, **kw)
        method.__name__ = name
        method.__qualname__ = f"WeftAdapter.{name}"
        method.__doc__ = f"Pass-through to weft `{name}` (see ports.py)."
        return method

    for port in (SitePort, EnvPort, RunPort):
        for name in getattr(port, "__protocol_attrs__", ()):
            if not hasattr(WeftAdapter, name):
                setattr(WeftAdapter, name, _make(name))


_install_port_methods()


# -- process-wide lifecycle -----------------------------------------------------

_adapter: Optional[WeftAdapter] = None
_status: dict = {"ok": False, "severity": "info",
                 "detail": "compute substrate not configured yet"}


def configure() -> dict:
    """Create the process-wide adapter (idempotent). Returns the status dict
    (also kept for `status()`/selfcheck). Never raises — degradation is
    recorded and surfaced, not fatal (the substrate becomes load-bearing in W1)."""
    global _adapter, _status
    if _adapter is not None:
        return _status
    pixi = resolve_pixi()
    if pixi is None:
        _status = {"ok": False, "severity": "warning",
                   "detail": "pixi binary not found (set ABA_PIXI_BIN or install "
                             "to $ABA_HOME/tools/pixi/bin/pixi) — weft substrate offline"}
        return _status
    try:
        _adapter = WeftAdapter(weft_workspace(), pixi)
        _status = {"ok": True, "severity": "info",
                   "detail": f"weft workspace {weft_workspace()} (pixi: {pixi})"}
    except ModuleNotFoundError:
        _status = {"ok": False, "severity": "warning",
                   "detail": "weft package not installed in this environment — "
                             "weft substrate offline"}
    except Exception as e:  # noqa: BLE001 — boot must not die on substrate wiring
        _status = {"ok": False, "severity": "warning",
                   "detail": f"weft substrate failed to start: {type(e).__name__}: {e}"}
    return _status


def get_compute() -> WeftAdapter:
    """The process adapter. Raises ComputeError (with the configure status as
    the detail) when the substrate is offline — callers surface that, they
    don't guess."""
    if _adapter is None:
        raise ComputeError("substrate_offline", _status["detail"], stage="aba",
                           hints={"fix": "check `compute_substrate` in /api/health"})
    return _adapter


def status() -> dict:
    return dict(_status)


def check_compute() -> dict:
    """selfcheck adapter (core.runtime.selfcheck) — surfaces substrate health
    on /api/health + the admin drawer."""
    return dict(_status)


def shutdown() -> None:
    global _adapter
    if _adapter is not None:
        _adapter.close()
        _adapter = None
