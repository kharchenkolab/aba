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
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from core.config import ENVS_DIR
from core.exec.base import Env, ExecResult, Provisioning
from core.exec.local import LocalSubprocessExecutor

PYLIB_DIR = ENVS_DIR / "pylib"          # shared pip --target overlay for libraries
TOOLS_ENV = ENVS_DIR / "tools"          # one shared conda env for CLI tools


def pylib_dir() -> Path:
    return PYLIB_DIR


def tools_env() -> Path:
    return TOOLS_ENV


class MaterializingExecutor:
    """Executor that materializes pip provisioning into the wipeable overlay
    and runs commands via the local subprocess executor."""

    def __init__(self):
        self._local = LocalSubprocessExecutor()

    def _tools_overlay(self) -> dict:
        """PATH overlay for the conda tools env, when it exists. Always applied
        to the base env so run_python sees any materialized CLI tool — mirror of
        how the pylib overlay is always on sys.path."""
        binp = TOOLS_ENV / "bin"
        return {"PATH": str(binp)} if binp.exists() else {}

    def _base_env(self) -> Env:
        return Env(id="base-venv", kind="venv", python=sys.executable,
                   env_overlay=self._tools_overlay())

    def materialize(self, prov: Provisioning, scope: str = "system") -> Env:
        if prov is None or prov.is_base():
            return self._base_env()

        if prov.container or prov.binary or prov.cran:
            raise NotImplementedError(
                "container/binary/cran provisioning is deferred (capdat_impl.md seams)."
            )

        if prov.conda:
            self._conda_install(prov.conda)
            return Env(id="conda-tools", kind="conda", root=str(TOOLS_ENV),
                       python=sys.executable, env_overlay=self._tools_overlay())

        if prov.pip:
            self._pip_install(prov.pip)
            # Return the base venv: run_python appends the pylib overlay to
            # sys.path itself, so one interpreter sees both .venv and overlay.
            return self._base_env()

        return self._base_env()

    def _conda_install(self, conda: dict) -> None:
        """Install a CLI tool into the shared conda tools env via micromamba.
        Cached: skips if the package is already present."""
        from core.exec.mamba import run_micromamba, installed_packages
        spec = (conda.get("spec") or "").strip()
        channel = conda.get("channel") or "conda-forge"
        if not spec:
            raise RuntimeError("conda provisioning needs a 'spec'")
        pkg = re.split(r"[=<>!]", spec)[0].strip()
        if pkg and pkg in installed_packages(TOOLS_ENV):
            return  # cache hit
        # micromamba install -p doesn't auto-create the prefix; use create the
        # first time, install thereafter (adds to the shared tools env).
        verb = "install" if (TOOLS_ENV / "conda-meta").exists() else "create"
        run_micromamba([verb, "-y", "-p", str(TOOLS_ENV),
                        "-c", channel, "-c", "conda-forge", spec])

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
