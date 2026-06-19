"""Local persistent kernel via jupyter_client (kernels.md §5).

Drives a real out-of-process IPython kernel: state persists across execute()
calls, Stop maps to SIGINT (interrupt) leaving state intact, and crashes are
isolated from the backend. The session runs in the same environment as the
stateless run_python (pylib overlay on sys.path, conda tools bin on PATH,
DATA_DIR injected) via a setup cell run once at startup.
"""
from __future__ import annotations
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# After Stop, how long to let a cell abort cleanly under SIGINT (which preserves
# the kernel + its state) before hard-killing the session. A cell wedged in
# native code ignores SIGINT, so without this ceiling execute() would block for
# the full timeout_s — the "Stop button does nothing" failure.
_CANCEL_GRACE_S = float(os.environ.get("ABA_KERNEL_CANCEL_GRACE_S", "3"))

from core.config import DATA_DIR, ARTIFACTS_DIR
from core.exec.base import ExecResult
from core.exec.materialize import pylib_paths, tools_env

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SPEC_NAME = "aba_py"
_R_SPEC_NAME = "aba_r"          # private R spec we own (not the clobberable 'ir')
_spec_ready = False
_r_spec_ready = False


def _ensure_python_kernelspec() -> str:
    """Register a kernelspec pointing at THIS interpreter (the .venv python, so
    the kernel has scanpy/pydeseq2/etc.). Idempotent; userspace (--user)."""
    global _spec_ready
    if _spec_ready:
        return _SPEC_NAME
    import subprocess
    try:
        from jupyter_client.kernelspec import KernelSpecManager
        if _SPEC_NAME in KernelSpecManager().find_kernel_specs():
            _spec_ready = True
            return _SPEC_NAME
    except Exception:  # noqa: BLE001
        pass
    subprocess.run(
        [sys.executable, "-m", "ipykernel", "install", "--user",
         "--name", _SPEC_NAME, "--display-name", "ABA Python"],
        capture_output=True, text=True, timeout=120,
    )
    _spec_ready = True
    return _SPEC_NAME


def _r_spec_points_into(spec_name: str, tenv: Path) -> bool:
    """True iff kernelspec `spec_name` exists and its R binary (argv[0]) is a real
    file inside our tools env. Guards against a stale/foreign spec that points at a
    wiped or wrong R — the exact failure where an e2e test left a global 'ir' spec
    pointing at a /tmp env that was later deleted, DOA-ing all live run_r."""
    import os.path
    from jupyter_client.kernelspec import KernelSpecManager
    try:
        spec = KernelSpecManager().get_kernel_spec(spec_name)
    except Exception:  # noqa: BLE001 — NoSuchKernel or a malformed spec
        return False
    argv = spec.argv or []
    if not argv or not argv[0]:
        return False
    r_bin = os.path.realpath(argv[0])
    tenv_r = os.path.realpath(str(tenv))
    return os.path.exists(r_bin) and (r_bin == tenv_r or r_bin.startswith(tenv_r + os.sep))


def _ensure_r_kernelspec() -> str:
    """Ensure a *private* IRkernel kernelspec (`aba_r`) pointing at OUR conda
    tools-env R. We register under our own name — never the generic, clobberable
    'ir' — and validate argv[0] points into the tools env each time, so a stale or
    foreign spec can't hijack run_r. Installs r-irkernel into the tools env on
    first use (slow once — a Bioconductor-scale conda solve); cached thereafter."""
    global _r_spec_ready
    if _r_spec_ready:
        return _R_SPEC_NAME
    from core.exec.materialize import tools_env
    tenv = tools_env()
    if _r_spec_points_into(_R_SPEC_NAME, tenv):
        _r_spec_ready = True
        return _R_SPEC_NAME
    import subprocess
    from core.exec.mamba import run_micromamba, installed_packages
    if "r-irkernel" not in installed_packages(tenv):
        verb = "install" if (tenv / "conda-meta").exists() else "create"
        run_micromamba([verb, "-y", "-p", str(tenv), "-c", "conda-forge", "r-irkernel"])
    # Register the spec under OUR name, in the user dir, pointing at THIS env's R +
    # IRkernel (installspec writes argv[0] = R.home()/bin/R of the running Rscript).
    # IRkernel::installspec shells out to `jupyter kernelspec install`, and
    # `jupyter` lives in the backend's own env, NOT the tools env — so put it on
    # PATH or installspec exits 127 ("jupyter-client has to be installed"),
    # writes nothing, and run_r later dies with "No such kernel named aba_r".
    import os, sys
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [str(tenv / "bin" / "Rscript"), "-e",
         f'IRkernel::installspec(name="{_R_SPEC_NAME}", displayname="ABA R", user=TRUE)'],
        capture_output=True, text=True, timeout=300, env=env)
    # Verify it actually landed — don't mark ready on a silent failure.
    if proc.returncode != 0 or not _r_spec_points_into(_R_SPEC_NAME, tenv):
        raise RuntimeError(
            "R kernel registration failed (IRkernel::installspec): "
            + ((proc.stderr or proc.stdout or "").strip()[-500:] or "no kernelspec written"))
    _r_spec_ready = True
    return _R_SPEC_NAME


def _project_data_artifacts() -> tuple[Path, Path]:
    """Resolve the active project's data + artifacts dirs at session-start time.
    Falls back to the workspace-level DATA_DIR/ARTIFACTS_DIR (no project active)."""
    from core import projects
    from core.config import project_data_dir, project_artifacts_dir
    pid = projects.current()
    if pid:
        return project_data_dir(pid), project_artifacts_dir(pid)
    return DATA_DIR, ARTIFACTS_DIR


def _r_setup_code(cwd: str) -> str:
    """First cell for an R session: project R library ahead of the shared base
    on .libPaths() (r_provisioning.md), then cwd + DATA_DIR (parallel to the
    Python one). The project lib is where on-demand `r_package` installs land,
    so a freshly-installed package is importable in the next cell.

    Also defaults the session's CRAN repo to ABA's PPM snapshot + sets the binary
    User-Agent — so even a hand-rolled `install.packages("pagoda2")` in run_r gets
    a PPM BINARY (source-compiling only when no binary exists), instead of the
    slow source build a bare `install.packages(..., repos='cloud.r-project.org')`
    triggers. `type='source'` still forces a source build on demand."""
    import os as _os
    from core import projects
    from core.exec.r import libpaths_expr, cran_repo, _ppm_ua_expr
    libline = libpaths_expr(projects.current() or "default")
    libline = (libline + "\n") if libline else ""
    repoline = f'options(repos=c(CRAN={cran_repo()!r})); {_ppm_ua_expr()}\n'
    # Pin IRkernel's plot DPI so harvested PNGs have a consistent pixel
    # size across sessions. IRkernel's default `repr.plot.res` varies by
    # version (72 in older, 120 in newer) — that variation translated into
    # huge-vs-normal fonts in chat depending on which session you were in,
    # because the chat scales each PNG to a fixed CSS width and a 504-px
    # plot gets upscaled much more than a 960-px one. ABA_R_PLOT_RES
    # overrides for the rare case where a project wants different defaults.
    try:
        _res = max(40, int(_os.environ.get("ABA_R_PLOT_RES", "120")))
    except ValueError:
        _res = 120
    plotline = f"options(repr.plot.res={_res})\n"
    data_dir, _ = _project_data_artifacts()
    return (f"{libline}{repoline}{plotline}DATA_DIR <- {str(data_dir)!r}\n"
            f"WORK_DIR <- {str(cwd)!r}\nsetwd({str(cwd)!r})\n"
            + _harvest_helpers_r())


def _harvest_helpers_r() -> str:
    """R `harvest_table()` helper, mirror of the Python one. Writes a
    CSV to the current cwd so the post-cell harvester picks it up.

    Auto-naming uses nanosecond Sys.time() + a random suffix to avoid
    collisions when called several times in the same cell — `digest`
    isn't guaranteed to be installed in every R image, so we stick
    with base R primitives."""
    return (
        "harvest_table <- function(df, name='auto') {\n"
        "  if (identical(name, 'auto')) {\n"
        "    .t <- format(as.numeric(Sys.time()) * 1e6, scientific=FALSE, digits=20)\n"
        "    .r <- paste(sample(c(0:9, letters[1:6]), 6, replace=TRUE), collapse='')\n"
        "    name <- paste0('table_', substr(.t, nchar(.t)-5, nchar(.t)), '_', .r, '.csv')\n"
        "  }\n"
        "  if (!grepl('\\\\.(csv|tsv)$', name, ignore.case=TRUE)) {\n"
        "    name <- paste0(name, '.csv')\n"
        "  }\n"
        "  path <- file.path(getwd(), name)\n"
        "  tryCatch(\n"
        "    write.csv(as.data.frame(df), path, row.names=FALSE),\n"
        "    error = function(e) write.csv(df, path)\n"
        "  )\n"
        "  cat(sprintf('[harvest_table] wrote %s\\n', basename(path)))\n"
        "  invisible(path)\n"
        "}\n"
    )


def _setup_code(cwd: str) -> str:
    """First cell: replicate the run_python environment in the kernel namespace.

    Also injects the Stage 6 harvest helpers (`harvest_table`) so recipes/
    agents can explicitly tag a DataFrame for pinning without manually
    composing a `df.to_csv(...)` line. See `_harvest_helpers_py` below.
    """
    data_dir, _ = _project_data_artifacts()
    pylib_appends = "".join(f"_sys.path.append({str(p)!r})\n" for p in pylib_paths())
    return (
        "import sys as _sys, os as _os\n"
        f"{pylib_appends}"
        f"_os.environ['PATH'] = {str(tools_env() / 'bin')!r} + _os.pathsep + _os.environ.get('PATH','')\n"
        "_os.environ.setdefault('MPLBACKEND', 'Agg')\n"
        f"DATA_DIR = {str(data_dir)!r}\n"
        f"WORK_DIR = {str(cwd)!r}\n"
        + _harvest_helpers_py()
    )


def _harvest_helpers_py() -> str:
    """Python `harvest_table()` helper, injected at kernel startup.

    Stage 6 of misc/exec_records_and_versioning.md — give recipes/agents
    a one-liner to mark a DataFrame for pinning. The function writes a
    CSV to the current cwd; the standard run_python post-cell harvester
    picks it up as a table artifact and registers it as a table entity.
    """
    return (
        "def harvest_table(obj, name='auto'):\n"
        "    \"\"\"Save a DataFrame (or anything with .to_csv()) to the current\n"
        "    workdir as a CSV so it surfaces as a pinnable table artifact.\n"
        "    Pass `name` to control the filename (default: auto-unique).\"\"\"\n"
        "    import os as _os, time as _t, hashlib as _h\n"
        "    from pathlib import Path as _P\n"
        "    if name == 'auto':\n"
        "        _seed = f'{_t.time_ns()}:{id(obj)}'.encode()\n"
        "        name = 'table_' + _h.md5(_seed).hexdigest()[:8] + '.csv'\n"
        "    if not name.lower().endswith(('.csv', '.tsv')):\n"
        "        name = name + '.csv'\n"
        "    _path = _P(_os.getcwd()) / name\n"
        "    if hasattr(obj, 'to_csv'):\n"
        "        # pandas / polars / etc. — let the library handle dialect + index\n"
        "        try:\n"
        "            obj.to_csv(_path, index=False)\n"
        "        except TypeError:\n"
        "            obj.to_csv(_path)\n"
        "    else:\n"
        "        import csv as _csv\n"
        "        with open(_path, 'w', newline='') as _f:\n"
        "            _w = _csv.writer(_f)\n"
        "            if isinstance(obj, dict):\n"
        "                _w.writerow(list(obj.keys()))\n"
        "                _w.writerow(list(obj.values()))\n"
        "            else:\n"
        "                for _row in obj:\n"
        "                    if isinstance(_row, (list, tuple)):\n"
        "                        _w.writerow(_row)\n"
        "                    else:\n"
        "                        _w.writerow([_row])\n"
        "    print(f'[harvest_table] wrote {_path.name}')\n"
        "    return str(_path)\n"
    )


def _kernel_threads() -> int:
    """Thread count for the BLAS/OMP pools inside a kernel. Default
    ``min(cpu_count, 8)`` — enough to keep numpy/torch/R BLAS and host-side data
    loading multithreaded, capped so a fat box doesn't oversubscribe (64+ OMP
    threads on small matrices is slower, not faster, and collides with DataLoader
    workers). Override with ``ABA_KERNEL_THREADS``."""
    import os
    override = os.environ.get("ABA_KERNEL_THREADS")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return max(1, min(os.cpu_count() or 4, 8))


def _kernel_env(lang: str, cwd: str) -> dict:
    """Environment for the kernel subprocess. Exposes DATA_DIR / WORK_DIR /
    ARTIFACTS_DIR as real env vars so Sys.getenv() (R) and os.environ (Python)
    both resolve them — not just the injected convenience variables (the gap that
    made Sys.getenv("DATA_DIR") return "" in run_r). For R, also put the conda
    tools-env on LD_LIBRARY_PATH + PATH so R-package .so's resolve their conda
    system-lib deps (e.g. igraph → libglpk.so.40) — the kernel isn't conda-
    activated, so without this, packages that load via `micromamba run` fail to
    dlopen in run_r (r_provisioning.md F5).

    Also pins the BLAS/OMP thread pools (OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS).
    torch/numpy read these at import, so we set them at kernel LAUNCH — overriding
    any inherited ``*_NUM_THREADS=1`` (jupyter, jax, and some launchers export 1,
    which is what pegged scvi training to a single core while the GPU starved).
    Set unconditionally so the kernel is deterministic regardless of what the
    parent process exported; tune with ABA_KERNEL_THREADS."""
    import os
    data_dir, artifacts_dir = _project_data_artifacts()
    env = dict(os.environ)
    env["DATA_DIR"] = str(data_dir)
    env["ARTIFACTS_DIR"] = str(artifacts_dir)
    env["WORK_DIR"] = str(cwd)
    # ABA_PYTHON: the python interpreter that has ABA's deps installed
    # (numpy / scipy / typst / ...). The R kernel runs on a separate conda
    # tools env whose `python3` doesn't see these, so code in run_r that
    # wants to shell out for a Python-only library (e.g. typst.compile)
    # needs an explicit pointer. compose-figure-typst's R template reads
    # this. Set unconditionally so the contract is the same in both
    # kernels.
    env["ABA_PYTHON"] = sys.executable
    nthreads = str(_kernel_threads())
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = nthreads
    if lang == "r":
        tenv = tools_env()
        env["LD_LIBRARY_PATH"] = str(tenv / "lib") + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        env["PATH"] = str(tenv / "bin") + os.pathsep + env.get("PATH", "")
    return env


class JupyterKernelSession:
    def __init__(self, scope_key: str, lang: str, *, cwd: str):
        from jupyter_client import KernelManager
        self.scope_key = scope_key
        self.lang = lang
        self.last_used = time.time()
        self.alive = False
        self._cancel_grace_s = _CANCEL_GRACE_S
        Path(cwd).mkdir(parents=True, exist_ok=True)
        if lang == "r":
            kernel_name, setup, setup_to = _ensure_r_kernelspec(), _r_setup_code(cwd), 60
        else:
            kernel_name, setup, setup_to = _ensure_python_kernelspec(), _setup_code(cwd), 30
        self._km = KernelManager(kernel_name=kernel_name)
        self._km.start_kernel(cwd=str(cwd), env=_kernel_env(lang, cwd))
        self._kc = self._km.client()
        self._kc.start_channels()
        self._kc.wait_for_ready(timeout=60)
        self.alive = True
        # Configure the session namespace (overlay + DATA_DIR for Python; cwd +
        # DATA_DIR for R).
        self.execute(setup, timeout_s=setup_to)

    def touch(self) -> None:
        self.last_used = time.time()

    def execute(self, code: str, *, cancel_token=None, timeout_s: int = 90) -> ExecResult:
        from core.runtime import progress
        from core.exec.stream_coalesce import Coalescer
        from core.config import TOOL_STREAM_FLUSH_BYTES, TOOL_STREAM_FLUSH_INTERVAL_S
        stdout: list[str] = []
        stderr: list[str] = []
        err_tb: Optional[str] = None
        _last_emit = [0.0]   # throttle live progress to ~2/s so a chatty loop can't flood

        def _emit_live(text: str):
            # Surface the newest line of output as a tool_progress tick so long
            # run_python work (downloads, training) is legible live instead of an
            # opaque wait. Carriage returns = in-place meters (curl/tqdm) → take
            # the latest segment. No-op when no progress sink is bound.
            line = next((s.strip() for s in reversed(text.replace("\r", "\n").splitlines())
                         if s.strip()), "")
            if not line:
                return
            now = time.time()
            if now - _last_emit[0] < 0.5:
                return
            _last_emit[0] = now
            progress.emit(line[:200], phase="run")

        # Live-tail of full stdout/stderr to the per-turn progress queue, in
        # 1s/10KB coalesced bursts (see core/config.py + stream_coalesce.py).
        # Independent of the one-line _emit_live tick: the latter feeds the
        # chat-line "running R · Loading dataset" indicator; this feeds the
        # output-drawer live pane keyed by tool_use_id.
        # Capture the sink ONCE here (main thread, sink is set by the caller)
        # and close over it — coalescer flushes fire from BOTH the worker
        # thread (byte-cap, via hook→push) AND the main thread (interval via
        # maybe_flush + final flush in `finally`). progress.current_sink() is
        # thread-local, so re-looking-it-up inside _emit_chunk silently
        # returned None on the main-thread flushes (2026-06-03 — symptom was
        # NO live tool_chunk events for short-output runs because the byte
        # cap never fired in-thread). Closing over the queue fixes both.
        _chunk_q = progress.current_sink()
        def _emit_chunk(ev: dict) -> None:
            if _chunk_q is None:
                return
            try:
                _chunk_q.put_nowait({
                    "type": "chunk",
                    "stream": ev.get("stream", "stdout"),
                    "text": ev.get("text", ""),
                    "bytes_total": ev.get("bytes_total", 0),
                    "elapsed_s": ev.get("elapsed_s", 0.0),
                    "reason": ev.get("reason"),
                })
            except Exception:  # noqa: BLE001 — live-tail must never break a run
                pass

        coalescer = Coalescer(
            flush_bytes=TOOL_STREAM_FLUSH_BYTES,
            flush_interval_s=TOOL_STREAM_FLUSH_INTERVAL_S,
            on_flush=_emit_chunk,
        )

        def hook(msg):
            nonlocal err_tb
            mtype = msg["header"]["msg_type"]
            content = msg.get("content", {})
            if mtype == "stream":
                txt = content.get("text", "")
                name = "stderr" if content.get("name") == "stderr" else "stdout"
                (stderr if name == "stderr" else stdout).append(txt)
                _emit_live(txt)         # one-line chat tick
                coalescer.push(name, txt)   # full coalesced live stream
            elif mtype == "error":
                err_tb = _ANSI.sub("", "\n".join(content.get("traceback", [])))
            elif mtype in ("execute_result", "display_data"):
                txt = (content.get("data") or {}).get("text/plain")
                if txt:
                    stdout.append(str(txt) + "\n")
                    coalescer.push("stdout", str(txt) + "\n")

        # Run the blocking interactive execute in a worker thread so THIS thread
        # can poll the cancel token and bound how long we wait after Stop. A cell
        # wedged in native code ignores SIGINT, so execute_interactive would
        # otherwise block for the full timeout_s — the "Stop does nothing"
        # failure. The progress sink is thread-local, so rebind it in the worker.
        sink = progress.current_sink()
        box: dict = {}

        def _runner():
            if sink is not None:
                progress.set_sink(sink)
            try:
                reply = self._kc.execute_interactive(
                    code, store_history=True, allow_stdin=False,
                    timeout=timeout_s, output_hook=hook,
                )
                box["status"] = (reply.get("content") or {}).get("status", "ok")
            except TimeoutError:
                box["timed_out"] = True
            except Exception as e:  # noqa: BLE001 — e.g. channels die after a kill
                box["error"] = repr(e)

        worker = threading.Thread(target=_runner, name="kernel-exec", daemon=True)
        # SIGINT on cancel (graceful — keeps the kernel + its state if the cell
        # heeds it); we escalate to a hard kill below if it doesn't.
        unregister = cancel_token.register(self.interrupt) if cancel_token is not None else None
        cancelled = False
        worker.start()
        try:
            while worker.is_alive():
                worker.join(timeout=0.2)
                # Time-flush pending bytes — covers slow-trickle output where
                # neither the 10KB byte cap nor the in-hook 1s check fired
                # (e.g. a single 2KB print every 4s).
                coalescer.maybe_flush()
                if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                    cancelled = True
                    break
        finally:
            if unregister is not None:
                unregister()
            # Final flush — emit any pending tail bytes accumulated since the
            # last interval/byte-cap flush. Idempotent if buffers are empty.
            try:
                coalescer.flush(reason="final")
            except Exception:  # noqa: BLE001
                pass

        if cancelled:
            # SIGINT already fired via the token. Give the cell a short grace to
            # abort cleanly (preserving session state). If it won't stop — wedged
            # in native code — hard-kill the session so the abandoned cell can't
            # keep computing or corrupt the next one; the pool starts a fresh
            # session on the next call.
            worker.join(timeout=self._cancel_grace_s)
            if worker.is_alive():
                self.shutdown()
            self.touch()
            return ExecResult(returncode=1, stdout="".join(stdout),
                              stderr=(err_tb or "".join(stderr)),
                              cancelled=True, timed_out=False)

        if box.get("error"):
            # The kernel died/errored out from under us (not a cancel) → channels
            # are likely dead; drop the session so the next call gets a fresh one.
            self.alive = False
            self.touch()
            return ExecResult(returncode=1, stdout="".join(stdout),
                              stderr=((err_tb or "".join(stderr)) + f"\n[kernel error] {box['error']}"),
                              cancelled=False, timed_out=False)

        timed_out = bool(box.get("timed_out"))
        if timed_out:
            self.interrupt()   # SIGINT a cell that blew past its ceiling
        status = box.get("status", "ok")
        self.touch()
        rc = 0 if (status == "ok" and not timed_out) else (-1 if timed_out else 1)
        return ExecResult(
            returncode=rc,
            stdout="".join(stdout),
            stderr=(err_tb or "".join(stderr)),
            cancelled=False,
            timed_out=timed_out,
        )

    def interrupt(self) -> None:
        try:
            self._km.interrupt_kernel()
        except Exception:  # noqa: BLE001
            pass

    def shutdown(self) -> None:
        self.alive = False
        try:
            self._kc.stop_channels()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._km.shutdown_kernel(now=True)
        except Exception:  # noqa: BLE001
            pass
        # Belt + braces: if jupyter_client's shutdown_kernel didn't actually
        # kill the subprocess (or the protocol-level shutdown is too slow on
        # uvicorn's tight shutdown deadline), SIGKILL the kernel pid directly
        # so it doesn't survive as an orphan owned by init (PK 2026-06-03 —
        # ~10 multi-day-old orphan R kernels were eating ~15 GB resident).
        try:
            pid = self.kernel_pid()
            if pid:
                import os, signal
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # already gone — good
        except Exception:  # noqa: BLE001
            pass

    def kernel_pid(self) -> Optional[int]:
        """The OS pid of the kernel subprocess, or None if unknown.
        Read from KernelManager's Popen (jupyter_client stores it as `.kernel`
        — old API — or `.provisioner.proc` — newer)."""
        try:
            km = self._km
            proc = getattr(km, "kernel", None) or getattr(
                getattr(km, "provisioner", None), "proc", None)
            return getattr(proc, "pid", None)
        except Exception:  # noqa: BLE001
            return None
