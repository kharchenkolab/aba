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
import sys
import sysconfig
from pathlib import Path
from typing import Optional, Sequence

from core import config
from core.config import ENVS_DIR, _LazyDir
from core.exec.base import Env, ExecResult, Provisioning
from core.exec.local import LocalSubprocessExecutor

PYLIB_DIR = _LazyDir(lambda: ENVS_DIR / "pylib")          # shared pip --prefix overlay (per-group growth)


def pylib_dir() -> Path:
    """Prefix root for the overlay. Use for housekeeping (mkdir / rm -rf).
    For import-from paths, use ``pylib_paths()`` — under --prefix those live
    one or two levels deeper (lib/pythonX.Y/site-packages)."""
    return PYLIB_DIR


def _site_paths(prefix: Path) -> list[Path]:
    """Site-packages dir(s) under a pip ``--prefix`` root, computed from sysconfig
    against the running interpreter — matches the layout `pip install
    --prefix=<prefix>` actually writes. Two entries (purelib + platlib) where a
    distro splits lib / lib64; usually one."""
    purelib = sysconfig.get_path("purelib", vars={"base": str(prefix), "platbase": str(prefix)})
    platlib = sysconfig.get_path("platlib", vars={"base": str(prefix), "platbase": str(prefix)})
    return list({Path(purelib), Path(platlib)})   # dedupe; usually equal


def pylib_paths() -> list[Path]:
    """Site-packages dir(s) of the install-wide shared overlay — appended to
    sys.path so its packages are importable in run_python."""
    return _site_paths(PYLIB_DIR)


# Per-project Python overlays (env_refactor.md P1) — the Python analog of R's
# r_libs/prj_<id>. Each holds only a project's own on-demand additions; the
# install-wide base + shared pylib stay shared. Appended LAST on sys.path so a
# project's package wins for itself but cannot pollute other projects.
PROJECT_PYLIB_ROOT = _LazyDir(lambda: ENVS_DIR / "pylib_proj")


def project_pylib_dir(project_id: str) -> Path:
    """pip ``--prefix`` root for one project's overlay."""
    return PROJECT_PYLIB_ROOT / str(project_id)


def project_pylib_paths(project_id: Optional[str]) -> list[Path]:
    """Site-packages dir(s) of a project's overlay (empty list if no project)."""
    if not project_id:
        return []
    return _site_paths(project_pylib_dir(project_id))


class MaterializingExecutor:
    """Executor that materializes pip provisioning into the wipeable overlay
    and runs commands via the local subprocess executor."""

    def __init__(self):
        self._local = LocalSubprocessExecutor()

    def _base_env(self) -> Env:
        # weft-only: the base venv is the RUN HARNESS (subprocess management). It
        # no longer stitches a conda tools-env onto PATH — CLI tools live in weft
        # tool envs (named_envs.ensure_tool_env), added to PATH by the caller.
        return Env(id="base-venv", kind="venv", python=sys.executable,
                   env_overlay={})

    def materialize(self, prov: Provisioning, scope: str = "system", *,
                    cancel_token=None, project_id: Optional[str] = None) -> Env:
        if prov is None or prov.is_base():
            return self._base_env()

        if prov.container or prov.binary or prov.cran or prov.conda:
            raise NotImplementedError(
                "container/binary/cran provisioning is deferred (capdat_impl.md "
                "seams); conda/tool envs are weft's now — use "
                "named_envs.ensure_tool_env, not MaterializingExecutor."
            )

            return self._base_env()

        return self._base_env()

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
        stream: bool = False,
    ) -> ExecResult:
        return self._local.exec(
            env, command, cwd=cwd, mounts=mounts,
            cancel_token=cancel_token, timeout_s=timeout_s, env_vars=env_vars,
            stream=stream,
        )
