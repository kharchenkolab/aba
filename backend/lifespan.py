"""App lifecycle — startup/shutdown wiring extracted from main.py (Item 2A.2).

Lives at the composition-root level (beside main.py), NOT under core/, because
startup deliberately wires CONTENT: the curated R base (content.bio.capabilities),
display-path backfill (content.bio.graph.display), and the in-process `aba_core`
MCP server (content.bio.mcp_servers). It is domain-aware glue, not core platform
code — so the seam (core/ must not import content/) doesn't apply here.

main.py calls `register_lifecycle(app)`; behavior is identical to the previous
`@app.on_event` handlers.
"""
from __future__ import annotations

import asyncio
import threading

from core.jobs.runner import start_worker


async def on_startup():
    from core import projects
    projects.init()          # picks/creates the active project + init_db
    start_worker()
    # Recover sys.executable FIRST if the launcher left it '' (bare argv[0] in
    # os.execve) — an empty interpreter silently poisons every subprocess that
    # falls back to it (base self-heal pip, run_python, materialize), surfacing as
    # `PermissionError: [Errno 13] Permission denied: ''`. Must run before
    # anything spawns a subprocess (incl. the reaper + base self-heal below).
    try:
        from core.exec.env_integrity import ensure_sys_executable
        ensure_sys_executable()
    except Exception as e:  # noqa: BLE001
        print(f"[startup] sys.executable recovery failed (non-fatal): {e}")
    # Orphan-kernel reaper — SIGKILL any kernels left behind by a prior
    # uvicorn that didn't run our shutdown handler (forced kill / crash /
    # SIGKILL during dev bouncing). Called explicitly here (not lazily on
    # first get_pool()) so the cleanup happens BEFORE any user load.
    try:
        from core.exec.kernels.pool import _reap_orphan_kernels
        _reap_orphan_kernels()
    except Exception as e:  # noqa: BLE001
        print(f"[startup] orphan kernel reap failed (non-fatal): {e}")
    # Startup self-checks (core/runtime/selfcheck) — register the platform checks
    # and run them once so degraded config (e.g. a node-local ENVS_DIR under a Slurm
    # submitter, finding F6b) is LOUD in the log AND on /api/health + the admin
    # drawer, instead of surfacing later as a cryptic in-job ModuleNotFoundError.
    # Non-fatal by design (loud-but-boot); the install-time gate is the hard stop.
    try:
        from core.runtime import selfcheck
        from core.exec.env_integrity import check_envs_dir_shared, check_base_dir_shared
        selfcheck.register("envs_dir_shared", check_envs_dir_shared)
        selfcheck.register("base_dir_shared", check_base_dir_shared)
        for _r in selfcheck.run():
            if not _r["ok"]:
                print(f"[startup] SELFCHECK {_r['severity'].upper()}: {_r['name']} — {_r['detail']}")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] selfcheck failed (non-fatal): {e}")
    # Base self-heal + immutability (env_refactor.md) and isolated-env GC, run in
    # the BACKGROUND so startup-to-ready isn't blocked. self_heal_base skips the
    # ~9s deep verify entirely when the base is unchanged (fingerprint stamp) or
    # on a read-only image (SIF/OOD) — both the steady state — and only does the
    # full deep verify + repair-from-lock + refreeze when the base actually
    # changed. The kernel-spawn path's import failures still get caught + repaired
    # post-hoc by env_root_cause, covering the brief first-boot window.
    async def _bg_base_maintenance():
        def _work():
            try:
                from core.exec.env_integrity import self_heal_base
                self_heal_base()
            except Exception as e:  # noqa: BLE001
                import traceback as _tb
                print(f"[startup] base self-heal failed (non-fatal): {e}\n{_tb.format_exc()}")
            # §11.6 lazy GC: reclaim built bytes of long-idle isolated envs (their
            # spec/lock stays, so next use rebuilds transparently).
            try:
                from core.exec.isolated_env import gc_isolated_envs
                gc = gc_isolated_envs()
                if gc:
                    print(f"[startup] reclaimed {len(gc)} long-idle isolated env(s): {gc}")
            except Exception as e:  # noqa: BLE001
                print(f"[startup] isolated-env GC failed (non-fatal): {e}")
        await asyncio.to_thread(_work)
    asyncio.create_task(_bg_base_maintenance())
    # Capture the asyncio loop so worker-thread producers
    # (auto_interpret, background jobs) can push events to the
    # /api/notifications SSE channel.
    from core.runtime import notifications as _notif
    _notif.set_loop(asyncio.get_event_loop())

    # Background-provision the curated shared R base (r_base.yaml: Seurat,
    # DESeq2/limma/edger/apeglm, tidyverse, cairo, Rcpp*). When everything
    # is already in the tools env, this completes in ~500ms (two
    # `micromamba list --json` calls, no solve). When the env is missing a
    # package, the solve + install runs in this thread — backend stays
    # responsive throughout. Daemon thread = dies with the process; never
    # blocks startup.
    def _provision_r_base_bg():
        import time as _t
        try:
            # Staged prewarm (lazy_env_init.md): while the base is still being built
            # (boot|completing), the INSTALLER owns the R build (complete-r-env) —
            # defer here so two micromamba solves don't race the same tools env. On
            # the next boot (base ready) this runs and finds R already built (no-op).
            from core.exec.env_integrity import base_stage
            if base_stage() != "ready":
                print("[r_base] base still staging — R build deferred to the installer "
                      "(complete-r-env); will verify on next boot", flush=True)
                return
            from content.bio.capabilities import provision_r_base
            t0 = _t.perf_counter()
            provision_r_base()
            dt = _t.perf_counter() - t0
            if dt > 5:
                print(f"[r_base] provisioned curated shared R base in {dt:.0f}s", flush=True)
            else:
                print(f"[r_base] curated shared R base already provisioned ({dt*1000:.0f}ms)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[r_base] provision failed (non-fatal — agent can still per-project install): {e}", flush=True)
    threading.Thread(target=_provision_r_base_bg, name="r_base_provision", daemon=True).start()
    # Pass-E follow-up: any Turn rows in GENERATING/EXECUTING_TOOLS/
    # SUMMARIZING state are from a process that didn't survive; they
    # cannot be resumed (stream + tool dispatch are in-memory). Mark
    # them FAILED so the UI doesn't show stale "in-flight" turns.
    try:
        from core.runtime.checkpoint import reap_stale_turns
        n = reap_stale_turns()
        if n:
            print(f"[startup] reaped {n} stale Turn row(s) from previous process")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] reap_stale_turns failed: {e}")
    # F3: backfill display_path for any entity created before the column
    # existed (or before bio's layout computers were registered).
    try:
        from content.bio.graph.display import backfill_missing_display_paths
        n = backfill_missing_display_paths()
        if n:
            print(f"[startup] backfilled display_path for {n} entit{'y' if n == 1 else 'ies'}")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] display_path backfill failed: {e}")

    # P3 #1 — bring up the MCP gateway. Empty config = no-op for stdio
    # servers. Phase 6.A also registers the in-process `aba_core` server
    # so bio's own tools flow through the same channel as external
    # stdio servers (see misc/phase6_mcp_wrapping.md). 6.A registers
    # zero tools today; subsequent sub-phases populate clusters.
    try:
        from core.runtime.mcp import (
            start_all as start_mcp, status as mcp_status,
            register_inprocess_server,
        )
        from pathlib import Path
        start_mcp(Path(__file__).parent / "content" / "bio" / "mcp" / "servers.yaml")
        try:
            from content.bio.mcp_servers.aba_core import make_server as make_aba_core
            # WU-1: expose_in_catalog=True so aba_core IS the agent's
            # tool catalog (TOOL_SCHEMAS is pruned). strip_prefix_in_catalog
            # =True so tools show as `Skill`/`run_python`/... rather than
            # `aba_core:Skill` — preserves build.py gate keys + behavior_slim
            # references + existing recipe text without a coordinated rename.
            register_inprocess_server(
                "aba_core", make_aba_core,
                expose_in_catalog=True,
                strip_prefix_in_catalog=True,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[startup] aba_core in-process server failed: {e}")
        s = mcp_status()
        n_up = sum(1 for srv in s["servers"] if srv["state"] == "connected")
        n_tot = len(s["servers"])
        if n_tot:
            print(f"[startup] MCP gateway: {n_up}/{n_tot} servers connected")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] MCP gateway init failed: {e}")

    # C-2: kick off the TurnSink TTL sweeper. Runs every hour, deletes
    # JSONL files older than 7d (`turn_events/*.jsonl`) and evicts
    # closed sinks older than 1h from the in-memory registry. One
    # sweep_once() at startup catches anything stale from the previous
    # process; the background loop keeps it tidy going forward.
    try:
        import asyncio as _asyncio
        from core.runtime import turn_sink as _ts
        first = _ts.sweep_once()
        if first["sinks_evicted"] or first["files_deleted"]:
            print(f"[startup] turn_sink sweep: {first}")
        _asyncio.create_task(_ts.sweep_forever(), name="turn_sink_sweeper")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] turn_sink sweeper init failed: {e}")


async def on_shutdown():
    """Cancel any in-flight Turn tasks before the worker exits.

    C-1 spawns the agent loop as a background asyncio task via
    turn_executor.start_turn — without this hook, uvicorn's `--reload`
    SIGTERM hangs indefinitely because the task is awaiting a thread-pool
    future (run_in_executor) that Python can't interrupt. We fire the
    cancel token (which the loop checks at every iteration boundary) so
    each task gets a chance to commit its in-progress state and exit
    cleanly, then give them a brief window. Anything still pending after
    that gets task.cancel() as a hard stop. The startup reaper will mark
    any survivors FAILED on next boot, so we don't leak Turn rows."""
    import asyncio
    from core.runtime import turn_sink, cancellation

    def _kill_owned_kernels():
        # SIGKILL all owned kernel subprocesses. atexit doesn't fire on SIGTERM,
        # and a signal-handler-in-worker is unreliable; the FastAPI shutdown
        # lifecycle IS invoked on uvicorn graceful exits, so we shoot the kernels
        # here. Prevents the orphan/zombie accumulation PK observed.
        try:
            from core.exec.kernels import get_pool
            import os, signal
            pids = get_pool().owned_kernel_pids()
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if pids:
                print(f"[shutdown] SIGKILLed {len(pids)} owned kernel subprocess(es)")
        except Exception as e:  # noqa: BLE001
            print(f"[shutdown] kernel cleanup failed (non-fatal): {e}")

    rids = turn_sink.active_ids()
    # 1. Fire cancel tokens — co-operative shutdown if the loop is at an
    #    iteration boundary or inside a cancellable tool.
    if rids:
        print(f"[shutdown] cancelling {len(rids)} in-flight Turn task(s): {rids}")
        for rid in rids:
            tok = cancellation.get(rid)
            if tok is not None:
                try: tok.cancel(reason="backend shutdown")
                except Exception: pass    # noqa: BLE001
    # 2. Kill kernels BEFORE awaiting the tasks. A turn wedged in a kernel exec
    #    (run_in_executor — uninterruptible from Python; the cancel token only
    #    lands at iteration boundaries) unblocks ONLY when its kernel dies. Killing
    #    kernels first lets the exec's kernel_dead watchdog fail-fast so the task
    #    can actually finish in the grace window — otherwise the await below hangs
    #    forever and uvicorn never exits (the orphaned, spinning-worker incident).
    #    Always reap, even with no active turns, so idle kernels don't leak.
    _kill_owned_kernels()
    # 3. Give the tasks a short grace to land now that their kernels are gone.
    if rids:
        tasks = [s._task for s in (turn_sink.get(rid) for rid in rids)
                 if s is not None and s._task is not None and not s._task.done()]
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True),
                                       timeout=3.0)
            except asyncio.TimeoutError:
                print(f"[shutdown] {sum(1 for t in tasks if not t.done())} task(s) "
                      f"didn't honor cancel — forcing task.cancel()")
                # 4. Hard cancel — the next startup's reaper will tidy the DB.
                for t in tasks:
                    if not t.done():
                        t.cancel()
    _kill_owned_kernels()   # 5. belt + braces — reap any kernel started since


def register_lifecycle(app) -> None:
    """Wire startup/shutdown onto the FastAPI app — the SAME mechanism as the old
    inline `@app.on_event(...)` decorators (this FastAPI has no add_event_handler)."""
    app.on_event("startup")(on_startup)
    app.on_event("shutdown")(on_shutdown)
