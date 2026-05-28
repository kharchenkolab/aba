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
        self._km = KernelManager(kernel_name=_ensure_python_kernelspec())
        self._km.start_kernel(cwd=str(cwd))
        self._kc = self._km.client()
        self._kc.start_channels()
        self._kc.wait_for_ready(timeout=60)
        self.alive = True
        # Configure the namespace to match run_python (overlay + DATA_DIR).
        self.execute(_setup_code(cwd), timeout_s=30)

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
