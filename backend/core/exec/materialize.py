"""MaterializingExecutor — builds capability environments on demand (P1).

Standardizes on **pip** (capdat_impl.md, per PK): Python library capabilities
materialize into a single shared pip ``--prefix`` overlay under ENVS_DIR/pylib,
which is wholly wipeable (``rm -rf`` → repopulates on next request) and kept OUT
of the system ``.venv`` so the backend env stays pristine.

The overlay is consumed by *appending* its site-packages dirs to ``sys.path``
(run_python preamble), not prepending via PYTHONPATH — so the ``.venv``'s
scientific stack (scanpy/numpy/pandas) always wins and the overlay only supplies
packages that are genuinely missing. That sidesteps version-shadowing while
still composing.

Why ``--prefix`` not ``--target``: ``--target`` ignores already-installed
packages on sys.path and re-downloads every transitive dep into the overlay
(scanpy → 100+ MB of duplicated numpy/pandas). ``--prefix`` respects the
running interpreter's site-packages, so a request for GEOparse installs *just*
GEOparse — numpy/pandas resolve to the .venv copy and aren't downloaded again.

Non-Python CLI tools (salmon/STAR/fastqc — not on PyPI) need conda; that path
is deferred (capdat_impl.md task 186) and raises NotImplementedError here.
"""
from __future__ import annotations
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Optional, Sequence

from core.config import ENVS_DIR
from core.exec.base import Env, ExecResult, Provisioning
from core.exec.local import LocalSubprocessExecutor

PYLIB_DIR = ENVS_DIR / "pylib"          # shared pip --prefix overlay for libraries
TOOLS_ENV = ENVS_DIR / "tools"          # one shared conda env for CLI tools


def pylib_dir() -> Path:
    """Prefix root for the overlay. Use for housekeeping (mkdir / rm -rf).
    For import-from paths, use ``pylib_paths()`` — under --prefix those live
    one or two levels deeper (lib/pythonX.Y/site-packages)."""
    return PYLIB_DIR


def pylib_paths() -> list[Path]:
    """Site-packages dirs the runtime should append to sys.path so the overlay's
    packages are importable. Two entries (purelib + platlib) on systems where
    they differ (some Linuxes split lib / lib64); usually one on macOS/Windows.
    Computed from sysconfig against the running interpreter — matches the layout
    `pip install --prefix=PYLIB_DIR` actually writes."""
    purelib = sysconfig.get_path(
        "purelib", vars={"base": str(PYLIB_DIR), "platbase": str(PYLIB_DIR)})
    platlib = sysconfig.get_path(
        "platlib", vars={"base": str(PYLIB_DIR), "platbase": str(PYLIB_DIR)})
    return list({Path(purelib), Path(platlib)})   # dedupe; usually equal


def tools_env() -> Path:
    return TOOLS_ENV


def _has_legacy_target_layout() -> bool:
    """Detect a pylib dir written by the old ``pip install --target`` codepath.

    --target writes top-level package dirs (numpy/, pandas/, …); --prefix writes
    a single ``lib/`` subdir. If the overlay has site-packages-style siblings
    but no ``lib/``, it's the old layout — wipe so --prefix can repopulate
    cleanly (avoids mixing two layouts that index into different sys.path entries).
    """
    if not PYLIB_DIR.exists():
        return False
    if (PYLIB_DIR / "lib").exists():
        return False
    # Heuristic: any *.dist-info at the top level → old --target layout.
    return any(p.suffix == ".dist-info" for p in PYLIB_DIR.iterdir())


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

    def materialize(self, prov: Provisioning, scope: str = "system", *, cancel_token=None) -> Env:
        if prov is None or prov.is_base():
            return self._base_env()

        if prov.container or prov.binary or prov.cran:
            raise NotImplementedError(
                "container/binary/cran provisioning is deferred (capdat_impl.md seams)."
            )

        if prov.conda:
            self._conda_install(prov.conda, cancel_token=cancel_token)
            return Env(id="conda-tools", kind="conda", root=str(TOOLS_ENV),
                       python=sys.executable, env_overlay=self._tools_overlay())

        if prov.pip:
            self._pip_install(prov.pip, cancel_token=cancel_token)
            # Return the base venv: run_python appends the pylib overlay to
            # sys.path itself, so one interpreter sees both .venv and overlay.
            return self._base_env()

        return self._base_env()

    def _conda_install(self, conda: dict, *, cancel_token=None) -> None:
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
        from core.runtime import progress
        progress.emit(f"conda: solving + installing {spec} (binary; can take a few minutes)…",
                      phase="conda")
        # micromamba install -p doesn't auto-create the prefix; use create the
        # first time, install thereafter (adds to the shared tools env).
        verb = "install" if (TOOLS_ENV / "conda-meta").exists() else "create"
        run_micromamba([verb, "-y", "-p", str(TOOLS_ENV),
                        "-c", channel, "-c", "conda-forge", spec], cancel_token=cancel_token)

    def _pip_install(self, packages: Sequence[str], *, cancel_token=None) -> None:
        """pip install the packages into the shared overlay. Idempotent enough:
        pip is fast on already-satisfied targets. Uses the .venv's pip but
        installs INTO the overlay dir, leaving the .venv untouched.

        Uses ``--prefix`` (not ``--target``) so pip checks the running
        interpreter's sys.path and SKIPS any dep already in the .venv —
        avoiding the duplicate-numpy/pandas problem with --target."""
        # One-time migration: the old --target layout dumps packages at the
        # top of PYLIB_DIR; --prefix puts them under lib/. Mixing the two
        # means imports randomly hit whichever the runtime added first. Wipe
        # the old layout on the first --prefix install to start clean.
        if _has_legacy_target_layout():
            shutil.rmtree(PYLIB_DIR, ignore_errors=True)
        PYLIB_DIR.mkdir(parents=True, exist_ok=True)
        from core.runtime import progress
        from core.exec.proc import run_cancellable
        progress.emit(f"pip: installing {', '.join(packages)} into the overlay…", phase="pip")
        cmd = [sys.executable, "-m", "pip", "install",
               "--prefix", str(PYLIB_DIR), *packages]
        proc = run_cancellable(cmd, timeout_s=900, cancel_token=cancel_token)
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
