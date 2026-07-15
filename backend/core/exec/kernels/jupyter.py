"""Local persistent kernel via jupyter_client (kernels.md §5).

Drives a real out-of-process IPython kernel: state persists across execute()
calls, Stop maps to SIGINT (interrupt) leaving state intact, and crashes are
isolated from the backend. The kernel IS a weft env (an isolated named env or
the project's default base-pack session) — standalone, with DATA_DIR injected
via a setup cell run once at startup.
"""
from __future__ import annotations
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from core import config

# After Stop, how long to let a cell abort cleanly under SIGINT (which preserves
# the kernel + its state) before hard-killing the session. A cell wedged in
# native code ignores SIGINT, so without this ceiling execute() would block for
# the full timeout_s — the "Stop button does nothing" failure.
_CANCEL_GRACE_S = config.settings.kernel_cancel_grace_s.get()

from core.config import DATA_DIR, ARTIFACTS_DIR
from core.exec.base import ExecResult

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# P1: hard deadlines for the otherwise-unbounded startup steps. get_or_start()
# holds the pool lock across the whole constructor, so a startup that BLOCKS (vs
# raises) under resource pressure wedges every other kernel request behind it.
# Normal startup is a few seconds; these caps just stop an infinite wedge.
_START_KERNEL_TIMEOUT_S = 60     # spawn the kernel subprocess
_START_CHANNELS_TIMEOUT_S = 30   # open the zmq channels
_WAIT_READY_TIMEOUT_S = 60       # kernel-ready handshake


_env_specs_ready: set = set()


def _ensure_base_python_kernelspec() -> str:
    """W3.0/W3.4 (weft rewrite): the DEFAULT python kernel runs the PROJECT's
    session over the base pack — a live env, so ensure_capability installs are
    importable in the running kernel without a restart. The pack MUST bake
    ipykernel (session clones inherit it). The spec name is keyed by SESSION id
    (a rebuilt/reset session re-registers; live installs mutate the same
    prefix, so the running kernel keeps working). Caller ensures the session
    BEFORE the pool lock."""
    import re as _re
    import subprocess
    from core import projects
    from core.compute import project_env
    pid = str(projects.current() or "_none")
    sess = project_env.ensure(pid, "python")
    py = sess["prefix"] / "bin" / "python"
    spec_name = _re.sub(r"[^a-z0-9._-]", "-",
                        f"aba-proj-{pid}-{sess['session_id'][-12:]}".lower())
    if spec_name in _env_specs_ready:
        return spec_name
    if subprocess.run([str(py), "-c", "import ipykernel"],
                      capture_output=True).returncode != 0:
        from core.compute import base_env
        raise RuntimeError(
            f"base pack {base_env.pack_name('python')!r} has no ipykernel — "
            f"a python base pack must include `ipykernel` in its spec "
            f"(the project session clones it; nothing is installed here)")
    subprocess.run(
        [str(py), "-m", "ipykernel", "install", "--user",
         "--name", spec_name, "--display-name", "ABA Python (base pack)"],
        capture_output=True, text=True, timeout=120)
    _env_specs_ready.add(spec_name)
    return spec_name


def _ensure_base_r_kernelspec() -> str:
    """W3.0/W3.4: the DEFAULT R kernel from the PROJECT's session over the R
    base pack (must bake r-irkernel). Registered per-session; standalone — no
    tools-env, no module shell machinery (the pack's MODULE TOGGLE still
    gates: OFF refuses with the enable prompt inside project_env.ensure)."""
    import re as _re
    import subprocess
    from core import projects
    from core.compute import project_env, base_env
    pid = str(projects.current() or "_none")
    sess = project_env.ensure(pid, "r")
    rs = sess["prefix"] / "bin" / "Rscript"
    spec_name = _re.sub(r"[^a-z0-9._-]", "-",
                        f"aba-proj-r-{pid}-{sess['session_id'][-12:]}".lower())
    if spec_name in _env_specs_ready:
        return spec_name
    import os
    env = os.environ.copy()
    # IRkernel::installspec shells out to `jupyter kernelspec install`; the
    # jupyter CLI lives in the backend env, so put it on PATH (same trick as
    # the tools-env spec below).
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [str(rs), "-e",
         f'IRkernel::installspec(name="{spec_name}", displayname="ABA R (base pack)", user=TRUE)'],
        capture_output=True, text=True, timeout=300, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"base pack {base_env.pack_name('r')!r} could not register its "
            f"IRkernel spec — an R base pack must include `r-irkernel`. "
            f"Detail: {(proc.stderr or proc.stdout or '')[-400:]}")
    _env_specs_ready.add(spec_name)
    return spec_name


def _base_r_setup_code(cwd: str) -> str:
    """First cell for a base-PACK R kernel: standalone env (its own library —
    no .libPaths() juggling; R additions layer via extends_env), keeping the
    CRAN repo default, plot DPI, and harvest helpers."""
    from core.exec.r import cran_repo, _ppm_ua_expr
    repoline = f'options(repos=c(CRAN={cran_repo()!r})); {_ppm_ua_expr()}\n'
    try:
        _res = max(40, config.settings.r_plot_res.get())
    except ValueError:
        _res = 120
    data_dir, _ = _project_data_artifacts()
    return (f"{repoline}options(repr.plot.res={_res})\n"
            f"DATA_DIR <- {str(data_dir)!r}\n"
            f"WORK_DIR <- {str(cwd)!r}\nsetwd({str(cwd)!r})\n"
            + _harvest_helpers_r())


def _ensure_env_python_kernelspec(env_name: str) -> str:
    """Register a kernelspec whose interpreter is the ISOLATED (weft) env's
    python (so a kernel launched from it sees that env's packages, standalone —
    §11.3). ipykernel is BAKED into every named python env at solve time
    (core/compute/named_envs._spec_for) — weft envs are frozen, nothing is ever
    installed into one here. Realization happens on first use (this call may
    build the prefix). Idempotent. The spec name includes the project id so two
    projects' same-named envs can't cross-wire to the wrong python."""
    import re as _re
    import subprocess
    from core.compute import named_envs
    from core import projects
    pid = str(projects.current() or "_none")
    row = named_envs.resolve(pid, env_name)
    if row is None:
        raise RuntimeError(f"isolated env {env_name!r} does not exist")
    py = named_envs.ensure_realized(row["env_id"]) / "bin" / "python"
    if not py.exists():
        raise RuntimeError(f"isolated env {env_name!r} realized without a python "
                           f"interpreter (env {row['env_id']})")
    # Spec name is per-project AND per-EnvID: extending the env moves the handle
    # to a NEW EnvID, and the kernelspec must follow it (a stale spec would keep
    # launching the pre-extension prefix).
    spec_name = _re.sub(r"[^a-z0-9._-]", "-",
                        f"aba-env-{pid}-{env_name}-{row['env_id'][-12:]}".lower())
    if spec_name in _env_specs_ready:
        return spec_name
    if subprocess.run([str(py), "-c", "import ipykernel"],
                      capture_output=True).returncode != 0:
        raise RuntimeError(
            f"env {env_name!r} has no ipykernel — recreate it with "
            f"make_isolated_env (named python envs bake ipykernel in)")
    subprocess.run(
        [str(py), "-m", "ipykernel", "install", "--user",
         "--name", spec_name, "--display-name", f"ABA env {env_name}"],
        capture_output=True, text=True, timeout=120)
    _env_specs_ready.add(spec_name)
    return spec_name


def _project_data_artifacts() -> tuple[Path, Path]:
    """Resolve the active project's data + artifacts dirs at session-start time.
    Falls back to the workspace-level DATA_DIR/ARTIFACTS_DIR (no project active)."""
    from core import projects
    from core.config import project_data_dir, project_artifacts_dir
    pid = projects.current()
    if pid:
        return project_data_dir(pid), project_artifacts_dir(pid)
    return DATA_DIR, ARTIFACTS_DIR


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


def _env_setup_code(cwd: str) -> str:
    """First cell for a weft-env kernel (isolated env OR base-pack session). The env
    is STANDALONE (its own site-packages + bin — the interpreter is the env's own,
    so its bin/ is already the active PATH); no overlay, no PIP_PREFIX, no tools-env
    injection. Just DATA_DIR / WORK_DIR / headless mpl + harvest helpers."""
    data_dir, _ = _project_data_artifacts()
    return (
        "import sys as _sys, os as _os\n"
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
    """Thread count for the BLAS/OMP pools inside a kernel. Sized to the CPU
    *allocation* (Slurm/cgroup/affinity), not the host core count, capped at 8 so
    a fat box doesn't oversubscribe (64+ OMP threads on small matrices is slower,
    not faster, and collides with DataLoader workers). On a node allocated 1 CPU
    out of 56 this returns 1 — without it OpenBLAS spawns 56 threads and dies on
    the per-user process limit. Override with ``ABA_KERNEL_THREADS``."""
    from core.exec.cpu import default_thread_cap
    return default_thread_cap()


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
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
        env[var] = nthreads
    if lang == "r":
        # The base-PACK R kernel is self-contained (conda env layout — its own
        # lib/ resolves .so deps via rpath). Point PATH/LD_LIBRARY_PATH at the
        # project's weft R session prefix; the pack is REQUIRED (no tools-env R).
        from core.compute import base_env as _be
        from core import projects as _pj
        from core.compute import project_env as _pe
        _be.require("r")
        _prefix = _pe.prefix(str(_pj.current() or "_none"), "r")
        env["LD_LIBRARY_PATH"] = str(_prefix / "lib") + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        env["PATH"] = str(_prefix / "bin") + os.pathsep + env.get("PATH", "")
        # R `future` defaults (Seurat/IntegrateLayers etc.): sequential plan + a
        # generous globals ceiling. future's 500 MiB future.globals.maxSize default
        # trips on real single-cell objects (IntegrateLayers ships the layered object
        # to workers — the classic error), and on one node parallel `future` usually
        # costs more than it saves. A step that benefits opts in with plan()/options()
        # in its own R code (which override these env defaults). Applies to in-process
        # run_r AND Slurm jobs (slurm_entry → run_r_code → this same kernel env).
        # Tune via ABA_R_FUTURE_PLAN / ABA_R_FUTURE_GLOBALS_MAXSIZE.
        env["R_FUTURE_PLAN"] = config.settings.r_future_plan.get()
        env["R_FUTURE_GLOBALS_MAXSIZE"] = config.settings.r_future_globals_maxsize.get()
    return env


class JupyterKernelSession:
    def __init__(self, scope_key: str, lang: str, *, cwd: str, env_name: str | None = None):
        from jupyter_client import KernelManager
        self.scope_key = scope_key
        self.lang = lang
        self.env_name = env_name
        self.last_used = time.time()
        self.alive = False
        self._cancel_grace_s = _CANCEL_GRACE_S
        Path(cwd).mkdir(parents=True, exist_ok=True)
        # W3.5 weft-only: every kernel is a weft env — an isolated named env, or
        # the project's default SESSION over the bundle-declared base pack. There
        # is no served-base kernel; a deployment that runs a language MUST declare
        # its base pack (require → loud, structured error, not a silent fallback).
        from core.compute import base_env as _base
        if lang == "r":
            _base.require("r")
            kernel_name, setup, setup_to = _ensure_base_r_kernelspec(), _base_r_setup_code(cwd), 60
        elif env_name:  # §11.3 isolated-env kernel: the env's python, standalone setup
            kernel_name, setup, setup_to = _ensure_env_python_kernelspec(env_name), _env_setup_code(cwd), 60
        else:           # base-pack python kernel: the pack's python, standalone setup
            _base.require("python")
            kernel_name, setup, setup_to = _ensure_base_python_kernelspec(), _env_setup_code(cwd), 60
        self._km = KernelManager(kernel_name=kernel_name)
        kenv = _kernel_env(lang, cwd)
        # Every kernel is now a weft env (isolated or base-pack session) — always
        # STANDALONE: a pack's additions layer via extends_env / session_install,
        # never PYTHONPATH stacking. The served-base project-overlay-on-PYTHONPATH
        # is gone with the served base.
        # P1: bound the startup. These library calls take no timeout and run while
        # the pool lock is held — if one BLOCKS under resource pressure it wedges
        # all kernel acquisition. Cap each so a bad startup fails fast (releasing
        # the lock) instead of hanging forever.
        self._start_bounded(lambda: self._km.start_kernel(cwd=str(cwd), env=kenv),
                            "start_kernel", _START_KERNEL_TIMEOUT_S)
        self._kc = self._km.client()
        self._start_bounded(self._kc.start_channels, "start_channels", _START_CHANNELS_TIMEOUT_S)
        self._kc.wait_for_ready(timeout=_WAIT_READY_TIMEOUT_S)
        self.alive = True
        # Configure the session namespace (overlay + DATA_DIR for Python; cwd +
        # DATA_DIR for R).
        self.execute(setup, timeout_s=setup_to)

    def _start_bounded(self, fn, what: str, timeout_s: float):
        """Run a blocking startup call (start_kernel / start_channels) under a hard
        deadline (P1). On timeout, best-effort kill the half-started kernel and
        raise, so get_or_start() fails fast + releases the pool lock. No stale
        session is stored — the raise precedes `self._sessions[key] = s`."""
        box: dict = {}

        def _r():
            try:
                box["v"] = fn()
            except BaseException as e:  # noqa: BLE001 — surface to the constructor
                box["e"] = e

        t = threading.Thread(target=_r, name=f"kstart-{what}", daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            try:
                self._km.shutdown_kernel(now=True)
            except Exception:  # noqa: BLE001 — best-effort; the worker is abandoned
                pass
            raise TimeoutError(
                f"kernel {what}() exceeded {timeout_s:.0f}s during startup "
                "(resource pressure?) — aborting so the pool isn't wedged")
        if "e" in box:
            raise box["e"]
        return box.get("v")

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
        kernel_died = False
        self.busy = True            # protect this session from LRU eviction while it runs
        worker.start()
        try:
            while worker.is_alive():
                worker.join(timeout=0.2)
                # Time-flush pending bytes — covers slow-trickle output where
                # neither the 10KB byte cap nor the in-hook 1s check fired
                # (e.g. a single 2KB print every 4s).
                coalescer.maybe_flush()
                self.touch()        # keep last_used fresh so a long run isn't seen as LRU
                if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                    cancelled = True
                    break
                # Watchdog: a kernel that died / was killed mid-run leaves the
                # worker blocked on a reply that never arrives — stop instead of
                # hanging (or spinning on its dead channels) forever. This is the
                # orphaned-uvicorn incident: a dead R kernel + a turn that never
                # returned, pegging CPU and wedging the chat (and the shutdown).
                if self.kernel_dead():
                    kernel_died = True
                    break
        finally:
            self.busy = False       # execution done — now eligible for eviction again
            if unregister is not None:
                unregister()
            # Final flush — emit any pending tail bytes accumulated since the
            # last interval/byte-cap flush. Idempotent if buffers are empty.
            try:
                coalescer.flush(reason="final")
            except Exception:  # noqa: BLE001
                pass

        if kernel_died:
            # The kernel process exited mid-run — the session is unusable. Reap it
            # (closes the dead channels so the daemon worker unblocks) and drop it
            # so the pool spawns a fresh kernel next call. Fail the turn with an
            # actionable message instead of a silent hang/spin.
            print(f"[kernel] {self.kernel_pid()} died mid-exec — failing turn + "
                  f"resetting session", flush=True)
            self.shutdown()
            self.alive = False
            self.touch()
            msg = (err_tb or "".join(stderr)).strip() or (
                "The compute kernel process died mid-execution (it was killed, "
                "crashed, or ran out of memory). The session has been reset — "
                "rerun the cell.")
            return ExecResult(returncode=1, stdout="".join(stdout), stderr=msg,
                              cancelled=False, timed_out=False)

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

    def _kernel_proc(self):
        """The kernel subprocess Popen across jupyter_client versions: old API
        `km.kernel`, newer `km.provisioner.process` (8.x) / `.proc`. Returns None
        if not resolvable (then callers fall back to the pid)."""
        try:
            km = self._km
            prov = getattr(km, "provisioner", None)
            return (getattr(km, "kernel", None)
                    or getattr(prov, "process", None)
                    or getattr(prov, "proc", None))
        except Exception:  # noqa: BLE001
            return None

    def kernel_pid(self) -> Optional[int]:
        """The OS pid of the kernel subprocess, or None if unknown. NB: prior to
        this fix the accessor missed jupyter_client 8.x's `provisioner.process`/
        `.pid`, so this returned None and owned_kernel_pids()/shutdown could never
        find kernels to reap — a root cause of the orphan/zombie pileup."""
        pid = getattr(self._kernel_proc(), "pid", None)
        if pid:
            return pid
        try:  # provisioner may expose the pid even when the Popen isn't reachable
            return getattr(getattr(self._km, "provisioner", None), "pid", None)
        except Exception:  # noqa: BLE001
            return None

    def kernel_dead(self) -> bool:
        """True if the kernel PROCESS has exited. Cheap (Popen.poll() or
        os.kill(pid,0) — no zmq heartbeat) — the exec watchdog uses it to stop
        waiting on a kernel that died/was killed mid-run instead of hanging (or
        spinning on its dead channels) forever. poll() also reaps the zombie."""
        proc = self._kernel_proc()
        if proc is not None:
            try:
                return proc.poll() is not None
            except Exception:  # noqa: BLE001
                pass
        pid = self.kernel_pid()
        if pid is None:
            return False                      # unknown — don't falsely report dead
        try:
            import os as _os
            _os.kill(pid, 0)
            return False                      # alive
        except ProcessLookupError:
            return True                       # gone
        except Exception:  # noqa: BLE001
            return False                      # exists but no signal perm → alive
