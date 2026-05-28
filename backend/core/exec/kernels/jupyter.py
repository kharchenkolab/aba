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
import time
from pathlib import Path
from typing import Optional

from core.config import DATA_DIR
from core.exec.base import ExecResult
from core.exec.materialize import pylib_dir, tools_env

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SPEC_NAME = "aba_py"
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


def _ensure_r_kernelspec() -> str:
    """Ensure the IRkernel ('ir') kernelspec exists, installing r-irkernel into
    the conda tools env and registering the spec on first use. Slow the first
    time (a Bioconductor-scale conda solve); cached thereafter."""
    global _r_spec_ready
    if _r_spec_ready:
        return "ir"
    from jupyter_client.kernelspec import KernelSpecManager
    try:
        if "ir" in KernelSpecManager().find_kernel_specs():
            _r_spec_ready = True
            return "ir"
    except Exception:  # noqa: BLE001
        pass
    import subprocess
    from core.exec.mamba import run_micromamba, installed_packages
    from core.exec.materialize import tools_env
    tenv = tools_env()
    if "r-irkernel" not in installed_packages(tenv):
        verb = "install" if (tenv / "conda-meta").exists() else "create"
        run_micromamba([verb, "-y", "-p", str(tenv), "-c", "conda-forge", "r-irkernel"])
    # Register the 'ir' spec (user dir) pointing at THIS env's R + IRkernel.
    subprocess.run([str(tenv / "bin" / "Rscript"), "-e", "IRkernel::installspec(user=TRUE)"],
                   capture_output=True, text=True, timeout=300)
    _r_spec_ready = True
    return "ir"


def _r_setup_code(cwd: str) -> str:
    """First cell for an R session: cwd + DATA_DIR (parallel to the Python one)."""
    return f"DATA_DIR <- {str(DATA_DIR)!r}\nsetwd({str(cwd)!r})\n"


def _setup_code(cwd: str) -> str:
    """First cell: replicate the run_python environment in the kernel namespace."""
    biomni = str(Path(__file__).resolve().parents[3] / "content" / "biomni")
    return (
        "import sys as _sys, os as _os\n"
        f"_sys.path.insert(0, {biomni!r})\n"
        f"_sys.path.append({str(pylib_dir())!r})\n"
        f"_os.environ['PATH'] = {str(tools_env() / 'bin')!r} + _os.pathsep + _os.environ.get('PATH','')\n"
        "_os.environ.setdefault('MPLBACKEND', 'Agg')\n"
        f"DATA_DIR = {str(DATA_DIR)!r}\n"
    )


class JupyterKernelSession:
    def __init__(self, scope_key: str, lang: str, *, cwd: str):
        from jupyter_client import KernelManager
        self.scope_key = scope_key
        self.lang = lang
        self.last_used = time.time()
        self.alive = False
        Path(cwd).mkdir(parents=True, exist_ok=True)
        if lang == "r":
            kernel_name, setup, setup_to = _ensure_r_kernelspec(), _r_setup_code(cwd), 60
        else:
            kernel_name, setup, setup_to = _ensure_python_kernelspec(), _setup_code(cwd), 30
        self._km = KernelManager(kernel_name=kernel_name)
        self._km.start_kernel(cwd=str(cwd))
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
        stdout: list[str] = []
        stderr: list[str] = []
        err_tb: Optional[str] = None

        def hook(msg):
            nonlocal err_tb
            mtype = msg["header"]["msg_type"]
            content = msg.get("content", {})
            if mtype == "stream":
                (stderr if content.get("name") == "stderr" else stdout).append(content.get("text", ""))
            elif mtype == "error":
                err_tb = _ANSI.sub("", "\n".join(content.get("traceback", [])))
            elif mtype in ("execute_result", "display_data"):
                txt = (content.get("data") or {}).get("text/plain")
                if txt:
                    stdout.append(str(txt) + "\n")

        unregister = cancel_token.register(self.interrupt) if cancel_token is not None else None
        timed_out = False
        status = "ok"
        try:
            try:
                reply = self._kc.execute_interactive(
                    code, store_history=True, allow_stdin=False,
                    timeout=timeout_s, output_hook=hook,
                )
                status = (reply.get("content") or {}).get("status", "ok")
            except TimeoutError:
                timed_out = True
                self.interrupt()
        finally:
            if unregister is not None:
                unregister()

        self.touch()
        cancelled = bool(cancel_token is not None and getattr(cancel_token, "cancelled", False))
        rc = 0 if (status == "ok" and not timed_out) else (-1 if timed_out else 1)
        return ExecResult(
            returncode=rc,
            stdout="".join(stdout),
            stderr=(err_tb or "".join(stderr)),
            cancelled=cancelled,
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
