"""Executor protocol + provisioning/env/result dataclasses (capdat_impl.md §3).

Frozen interface. `Provisioning` over-declares satisfiers (`container`,
`mcp_server`, `binary`) that P0 does not honor yet, so the shape never changes
as backends are added — the executor simply learns new branches.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence


@dataclass
class Provisioning:
    """Declarative environment spec. The executor satisfies it; the agent
    never resolves a dependency itself (capabilities.md P3)."""
    conda: Optional[dict] = None            # {"channel": "bioconda", "spec": "salmon=1.10.3"}
    pip: Optional[list[str]] = None
    cran: Optional[list[str]] = None
    container: Optional[str] = None         # OCI/Apptainer image ref (P6)
    mcp_server: Optional[dict] = None       # remote connection (K5)
    binary: Optional[dict] = None           # static binary + checksum

    def is_base(self) -> bool:
        """True when nothing extra is requested — the base venv suffices."""
        return not any((self.conda, self.pip, self.cran, self.container,
                        self.mcp_server, self.binary))


@dataclass
class Env:
    """A materialized environment handle."""
    id: str
    kind: str                               # "venv" | "conda" | "container" | "remote"
    root: Optional[str] = None              # filesystem root, when local
    python: Optional[str] = None            # interpreter path, when relevant
    env_overlay: dict = field(default_factory=dict)
    # Extra environment variables exec() merges in — e.g. PYTHONPATH for the
    # pylib overlay, PATH for a conda env's bin. Lets one materialized env
    # compose with the base venv without mutating it.


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    timed_out: bool = False


class Executor(Protocol):
    def materialize(self, prov: Provisioning, scope: str = "system") -> Env:
        """Build or fetch the environment described by `prov`, return a handle.
        Cached at the appropriate scope by the impl."""
        ...

    def exec(
        self,
        env: Env,
        command: Sequence[str],
        *,
        cwd: str,
        mounts: Sequence[tuple[str, str]] = (),
        cancel_token=None,
        timeout_s: int = 90,
        env_vars: Optional[dict] = None,
    ) -> ExecResult:
        """Run `command` in `env` with `cwd` as the working directory.
        Honors a CancelToken (Stop button) and a wall-clock timeout."""
        ...
