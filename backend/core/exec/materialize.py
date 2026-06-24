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
import os
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

PYLIB_DIR = ENVS_DIR / "pylib"          # shared pip --prefix overlay (per-group growth)


def _resolve_tools_env() -> Path:
    """Where the shared conda **tools env** (R base + CLI binaries) lives.

    Default: ``ENVS_DIR/tools`` — the legacy/dev/test location, beside the
    per-group growth layers (``pylib`` overlay, ``r_libs``). Unchanged behavior
    when ``ABA_TOOLS_DIR`` is unset, so dev + the ~30 tests that point
    ``ABA_ENVS_DIR`` at a throwaway dir stay byte-identical.

    Override: ``ABA_TOOLS_DIR`` pins the base to an explicit path. The R/CLI
    base is expensive to build and is IDENTICAL for every group, so production
    (the OOD launch) points this at a pre-baked, **image-resident** copy that
    ships beside the Python venv — same base/growth split as Python (venv base
    in the image; ``pylib`` overlay per-group). That stops R rebuilding for
    every lab. Growth (``r_libs``, ``pylib``) stays under ``ENVS_DIR``
    regardless of where the base resolves.
    """
    override = os.environ.get("ABA_TOOLS_DIR")
    if override and override.strip():
        return Path(override).resolve()
    return ENVS_DIR / "tools"


TOOLS_ENV = _resolve_tools_env()        # shared conda env: R base + CLI tools


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
PROJECT_PYLIB_ROOT = ENVS_DIR / "pylib_proj"


def project_pylib_dir(project_id: str) -> Path:
    """pip ``--prefix`` root for one project's overlay."""
    return PROJECT_PYLIB_ROOT / str(project_id)


def project_pylib_paths(project_id: Optional[str]) -> list[Path]:
    """Site-packages dir(s) of a project's overlay (empty list if no project)."""
    if not project_id:
        return []
    return _site_paths(project_pylib_dir(project_id))


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

    def materialize(self, prov: Provisioning, scope: str = "system", *,
                    cancel_token=None, project_id: Optional[str] = None) -> Env:
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
            # §11.4: ALL runtime installs go to the project's OWN overlay — the only
            # session-writable layer. Nothing writes to the shared overlay anymore
            # (it's folded into the immutable base); install-wide additions happen
            # via a deliberate base rebuild from the lock, not a runtime write to a
            # layer everyone reads. (No project context → legacy shared, rare.)
            _prefix = project_pylib_dir(str(project_id)) if project_id else None
            self._pip_install(prov.pip, cancel_token=cancel_token, prefix=_prefix)
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

    def _pip_install(self, packages: Sequence[str], *, cancel_token=None,
                     prefix: Optional[Path] = None) -> None:
        """pip install the packages into a ``--prefix`` overlay — the shared
        install-wide overlay by default, or a project's own overlay when
        ``prefix`` is given (env_refactor.md P1). Uses the .venv's pip but
        installs INTO the overlay, leaving the .venv untouched.

        Uses ``--prefix`` (not ``--target``) so pip checks the running
        interpreter's sys.path and SKIPS any dep already in the .venv —
        avoiding the duplicate-numpy/pandas problem with --target."""
        target = Path(prefix) if prefix is not None else PYLIB_DIR
        # One-time migration: the old --target layout dumps packages at the top
        # of the SHARED overlay; --prefix puts them under lib/. Mixing the two
        # means imports randomly hit whichever the runtime added first. Wipe the
        # old layout on the first --prefix install. (Project overlays are new —
        # they never carry the legacy layout, so only check the shared one.)
        if target == PYLIB_DIR and _has_legacy_target_layout():
            shutil.rmtree(PYLIB_DIR, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        from core.runtime import progress
        from core.exec.proc import run_cancellable
        from core.exec.env_integrity import ensure_base_constraints, abi_anchor_constraints
        # §11.4: a project overlay (the writable layer) constrains only the ABI
        # ANCHOR (numpy) — so a project can OVERRIDE ordinary package versions but a
        # bad override that needs a different numpy fails the resolve instead of
        # shadow-breaking the compiled stack. The legacy shared path (no prefix)
        # keeps the full base freeze. Best-effort: unconstrained + warn if absent.
        _constraints = abi_anchor_constraints() if prefix is not None else ensure_base_constraints()
        _cflag = ["-c", str(_constraints)] if _constraints else []
        if not _constraints:
            print("[materialize] WARNING: base-constraints unavailable; "
                  "installing UNCONSTRAINED (numpy-drift guard off)", flush=True)
        _where = "project overlay" if target != PYLIB_DIR else "shared overlay"
        progress.emit(f"pip: installing {', '.join(packages)} into the {_where}…", phase="pip")
        cmd = [sys.executable, "-m", "pip", "install",
               "--prefix", str(target), *_cflag, *packages]
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
