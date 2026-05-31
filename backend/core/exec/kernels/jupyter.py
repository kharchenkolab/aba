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
from core.exec.materialize import pylib_dir, tools_env

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
    subprocess.run(
        [str(tenv / "bin" / "Rscript"), "-e",
         f'IRkernel::installspec(name="{_R_SPEC_NAME}", displayname="ABA R", user=TRUE)'],
        capture_output=True, text=True, timeout=300)
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
    from core import projects
    from core.exec.r import libpaths_expr, cran_repo, _ppm_ua_expr
    libline = libpaths_expr(projects.current() or "default")
    libline = (libline + "\n") if libline else ""
    repoline = f'options(repos=c(CRAN={cran_repo()!r})); {_ppm_ua_expr()}\n'
    data_dir, _ = _project_data_artifacts()
    return (f"{libline}{repoline}DATA_DIR <- {str(data_dir)!r}\n"
            f"WORK_DIR <- {str(cwd)!r}\nsetwd({str(cwd)!r})\n")


def _setup_code(cwd: str) -> str:
    """First cell: replicate the run_python environment in the kernel namespace."""
    from core.config import BIOMNI_DIR
    biomni_line = f"_sys.path.insert(0, {str(BIOMNI_DIR)!r})\n" if BIOMNI_DIR else ""
    data_dir, _ = _project_data_artifacts()
    return (
        "import sys as _sys, os as _os\n"
        f"{biomni_line}"
        f"_sys.path.append({str(pylib_dir())!r})\n"
        f"_os.environ['PATH'] = {str(tools_env() / 'bin')!r} + _os.pathsep + _os.environ.get('PATH','')\n"
        "_os.environ.setdefault('MPLBACKEND', 'Agg')\n"
        f"DATA_DIR = {str(data_dir)!r}\n"
        f"WORK_DIR = {str(cwd)!r}\n"
    )


def _kernel_env(lang: str, cwd: str) -> dict:
    """Environment for the kernel subprocess. Exposes DATA_DIR / WORK_DIR /
    ARTIFACTS_DIR as real env vars so Sys.getenv() (R) and os.environ (Python)
    both resolve them — not just the injected convenience variables (the gap that
    made Sys.getenv("DATA_DIR") return "" in run_r). For R, also put the conda
    tools-env on LD_LIBRARY_PATH + PATH so R-package .so's resolve their conda
    system-lib deps (e.g. igraph → libglpk.so.40) — the kernel isn't conda-
    activated, so without this, packages that load via `micromamba run` fail to
    dlopen in run_r (r_provisioning.md F5)."""
    import os
    data_dir, artifacts_dir = _project_data_artifacts()
    env = dict(os.environ)
    env["DATA_DIR"] = str(data_dir)
    env["ARTIFACTS_DIR"] = str(artifacts_dir)
    env["WORK_DIR"] = str(cwd)
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

        def hook(msg):
            nonlocal err_tb
            mtype = msg["header"]["msg_type"]
            content = msg.get("content", {})
            if mtype == "stream":
                txt = content.get("text", "")
                (stderr if content.get("name") == "stderr" else stdout).append(txt)
                _emit_live(txt)   # stdout *and* stderr — progress bars often go to stderr
            elif mtype == "error":
                err_tb = _ANSI.sub("", "\n".join(content.get("traceback", [])))
            elif mtype in ("execute_result", "display_data"):
                txt = (content.get("data") or {}).get("text/plain")
                if txt:
                    stdout.append(str(txt) + "\n")

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
                if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                    cancelled = True
                    break
        finally:
            if unregister is not None:
                unregister()

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
