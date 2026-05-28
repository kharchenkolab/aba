"""MaterializingExecutor — builds capability environments on demand (P1).

Standardizes on **pip** (capdat_impl.md, per PK): Python library capabilities
materialize into a single shared pip ``--target`` overlay under ENVS_DIR/pylib,
which is wholly wipeable (``rm -rf`` → repopulates on next request) and kept OUT
of the system ``.venv`` so the backend env stays pristine.

The overlay is consumed by *appending* it to ``sys.path`` (run_python preamble),
not prepending via PYTHONPATH — so the ``.venv``'s scientific stack
(scanpy/numpy/pandas) always wins and the overlay only supplies packages that
are genuinely missing. That sidesteps version-shadowing while still composing.

Non-Python CLI tools (salmon/STAR/fastqc — not on PyPI) need conda; that path
is deferred (capdat_impl.md task 186) and raises NotImplementedError here.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from core.config import ENVS_DIR
from core.exec.base import Env, ExecResult, Provisioning
from core.exec.local import LocalSubprocessExecutor

PYLIB_DIR = ENVS_DIR / "pylib"          # shared pip --target overlay for libraries


def pylib_dir() -> Path:
    return PYLIB_DIR


class MaterializingExecutor:
    """Executor that materializes pip provisioning into the wipeable overlay
    and runs commands via the local subprocess executor."""

    def __init__(self):
        self._local = LocalSubprocessExecutor()

    def materialize(self, prov: Provisioning, scope: str = "system") -> Env:
        base = Env(id="base-venv", kind="venv", python=sys.executable)
        if prov is None or prov.is_base():
            return base

        if prov.conda or prov.container or prov.binary or prov.cran:
            raise NotImplementedError(
                "Non-pip provisioning (conda/container/cran/binary) is deferred; "
                "CLI tools that aren't on PyPI need conda — to be wired when first "
                "requested (capdat_impl.md task 186)."
            )

        if prov.pip:
            self._pip_install(prov.pip)
            # Return the base venv: run_python appends the overlay to sys.path
            # itself, so the same base interpreter sees both .venv and overlay.
            return base

        return base

    def _pip_install(self, packages: Sequence[str]) -> None:
        """pip install the packages into the shared overlay. Idempotent enough:
        pip is fast on already-satisfied targets. Uses the .venv's pip but
        installs INTO the overlay dir, leaving the .venv untouched."""
        PYLIB_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "pip", "install",
               "--target", str(PYLIB_DIR), *packages]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(
                f"pip install into overlay failed for {list(packages)}:\n"
                f"{(proc.stderr or proc.stdout or '')[-1500:]}"
            )

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
        return self._local.exec(
            env, command, cwd=cwd, mounts=mounts,
            cancel_token=cancel_token, timeout_s=timeout_s, env_vars=env_vars,
        )
