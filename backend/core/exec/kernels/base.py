"""KernelSession interface (kernels.md §5). Transport-agnostic — the local
jupyter_client impl and a future remote impl both satisfy it, so nothing above
this changes when kernels move off-box."""
from __future__ import annotations
from typing import Optional, Protocol, Sequence

from core.exec.base import ExecResult


class KernelSession(Protocol):
    scope_key: str            # thread id (or sub-agent/scenario run id)
    lang: str                 # "python" | "r"
    last_used: float
    alive: bool

    def execute(self, code: str, *, cancel_token=None, timeout_s: int = 90) -> ExecResult:
        """Run a code chunk in the live session; state persists across calls."""
        ...

    def interrupt(self) -> None:
        """SIGINT the running cell (Stop button). Session state survives."""
        ...

    def shutdown(self) -> None:
        """Terminate the kernel and release resources."""
        ...
