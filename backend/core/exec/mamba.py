"""micromamba bootstrap (capdat_impl.md P3 / task 186).

The sanctioned userspace exception to the pip-first rule: genuinely non-Python
CLI tools (salmon, fastqc, STAR…) aren't on PyPI and need conda. We fetch a
single static micromamba binary into the wipeable ENVS_DIR (no root, no system
install) and use it to populate one shared conda env for CLI tools. pip remains
primary for everything Python.
"""
from __future__ import annotations
import os
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Sequence

from core.config import ENVS_DIR, _LazyDir

MAMBA_BIN = _LazyDir(lambda: ENVS_DIR / "bin" / "micromamba")
MAMBA_ROOT = _LazyDir(lambda: ENVS_DIR / "mamba_root")


def _mamba_platform() -> str:
    """micro.mamba.pm platform slug for the host. Critically NOT hardcoded:
    on a Mac install a linux-64 binary downloads fine but fails to exec
    ('Exec format error'), breaking all R/CLI provisioning."""
    sysname, machine = platform.system(), platform.machine().lower()
    if sysname == "Darwin":
        return "osx-arm64" if machine in ("arm64", "aarch64") else "osx-64"
    if machine in ("arm64", "aarch64"):
        return "linux-aarch64"
    return "linux-64"


_MAMBA_URL = f"https://micro.mamba.pm/api/micromamba/{_mamba_platform()}/latest"


def _runs(path: Path) -> bool:
    """True if this micromamba binary actually executes here. Guards against a
    wrong-arch binary left in a cache/wiped dir (X_OK alone passes for those)."""
    try:
        return subprocess.run([str(path), "--version"], capture_output=True,
                              timeout=15).returncode == 0
    except Exception:  # noqa: BLE001  (OSError: Exec format error, etc.)
        return False


def ensure_micromamba() -> str:
    """Return the path to a usable micromamba binary, downloading it on first
    use. Idempotent."""
    # Prefer the binary the installer already placed under $ABA_HOME/bin — it's
    # the right arch for this machine and isn't wiped with ENVS_DIR.
    aba_home = os.environ.get("ABA_HOME")
    if aba_home:
        cand = Path(aba_home) / "bin" / "micromamba"
        if cand.exists() and _runs(cand):
            return str(cand)
    # Validate (not just exists+X_OK) so a stale wrong-arch binary re-downloads.
    if MAMBA_BIN.exists() and _runs(MAMBA_BIN):
        return str(MAMBA_BIN)
    MAMBA_BIN.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.bz2", delete=False) as tf:
            with urllib.request.urlopen(_MAMBA_URL, timeout=60) as resp:
                tf.write(resp.read())
            tarball = tf.name
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"Could not download micromamba (offline?): {e}. "
            f"CLI/conda tools can't be materialized without it."
        )
    # The tarball stores the binary at bin/micromamba.
    with tarfile.open(tarball, "r:bz2") as tar:
        member = next((m for m in tar.getmembers()
                       if m.name.endswith("bin/micromamba")), None)
        if member is None:
            raise RuntimeError("micromamba tarball missing bin/micromamba")
        with tar.extractfile(member) as src, open(MAMBA_BIN, "wb") as dst:
            dst.write(src.read())
    os.chmod(MAMBA_BIN, 0o755)
    Path(tarball).unlink(missing_ok=True)
    return str(MAMBA_BIN)


def _mamba_env() -> dict:
    env = os.environ.copy()
    env["MAMBA_ROOT_PREFIX"] = str(MAMBA_ROOT)
    # Force an English UTF-8 locale on subprocesses when the parent has
    # no LANG set. macOS LaunchAgents inherit a near-empty env by
    # default; without LANG, R falls back to the system's AppleLanguages
    # preference list and picks de-AT for technical messages on a
    # German-leaning machine (live bug 2026-06-11: "Fehler in parse:
    # unerwartetes Symbol" instead of "Error in parse: unexpected
    # symbol"). Only override missing-OR-empty values so an explicit
    # user locale survives (setdefault alone wouldn't replace LANG="").
    for k in ("LANG", "LC_ALL", "LC_MESSAGES"):
        if not (env.get(k) or "").strip():
            env[k] = "en_US.UTF-8"
    return env


def run_micromamba(args: Sequence[str], *, timeout_s: int = 1800,
                   check: bool = True, cancel_token=None) -> subprocess.CompletedProcess:
    """Run micromamba with the standalone root prefix set. Raises on failure
    when `check` (default); with check=False, returns the CompletedProcess so
    the caller can inspect returncode/stderr (used for `micromamba run` of an R
    install, where a non-zero exit is a normal, reportable outcome). A
    cancel_token makes a long install abortable by Stop (killpg the group)."""
    from core.exec.proc import run_cancellable
    mamba = ensure_micromamba()
    proc = run_cancellable([mamba, *args], env=_mamba_env(),
                           timeout_s=timeout_s, cancel_token=cancel_token)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"micromamba {' '.join(args)} failed:\n{(proc.stderr or proc.stdout or '')[-1500:]}"
        )
    return proc


def installed_packages(prefix: Path) -> set[str]:
    """Names of packages already in a conda prefix (for cache checks). Empty if
    the prefix doesn't exist yet."""
    import json as _json
    if not Path(prefix).exists():
        return set()
    try:
        proc = run_micromamba(["list", "-p", str(prefix), "--json"], timeout_s=120)
        return {p.get("name") for p in _json.loads(proc.stdout) if p.get("name")}
    except Exception:  # noqa: BLE001
        return set()
