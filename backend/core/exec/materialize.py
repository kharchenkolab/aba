"""MaterializingExecutor — the local run HARNESS for one-shot subprocesses.

Provisioning is the substrate's job now: environments are weft-solved and
weft-realized, and callers hand this executor an already-resolved interpreter
(a session/named-env prefix). What remains here is subprocess management in
the base venv — the launch harness — plus an honest refusal for any stray
pre-substrate Provisioning request (pip/conda/container overlays died with
the migration; CLI tools live in weft tool envs via
``named_envs.ensure_tool_env``)."""
from __future__ import annotations
import sys
import sysconfig
from pathlib import Path
from typing import Optional, Sequence

from core import config
from core.config import ENVS_DIR, _LazyDir
from core.exec.base import Env, ExecResult, Provisioning
from core.exec.local import LocalSubprocessExecutor


def _site_paths(prefix: Path) -> list[Path]:
    """Site-packages dir(s) under an env `prefix`, computed from sysconfig against
    the running interpreter — the standard `lib/pythonX.Y/site-packages` layout.
    Two entries (purelib + platlib) where a distro splits lib / lib64; usually one.
    Used to enumerate a weft SESSION's / named-env's installed packages."""
    purelib = sysconfig.get_path("purelib", vars={"base": str(prefix), "platbase": str(prefix)})
    platlib = sysconfig.get_path("platlib", vars={"base": str(prefix), "platbase": str(prefix)})
    return list({Path(purelib), Path(platlib)})   # dedupe; usually equal


class MaterializingExecutor:
    """Runs commands via the local subprocess executor in the base venv (the run
    HARNESS). Provisioning is weft's now — a stray pip/conda Provisioning raises."""

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
