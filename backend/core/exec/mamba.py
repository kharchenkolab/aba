"""micromamba bootstrap (capdat_impl.md P3 / task 186).

The sanctioned userspace exception to the pip-first rule: genuinely non-Python
CLI tools (salmon, fastqc, STAR…) aren't on PyPI and need conda. We fetch a
single static micromamba binary into the wipeable ENVS_DIR (no root, no system
install) and use it to populate one shared conda env for CLI tools. pip remains
primary for everything Python.
"""
from __future__ import annotations
import os
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Sequence

from core.config import ENVS_DIR

MAMBA_BIN = ENVS_DIR / "bin" / "micromamba"
MAMBA_ROOT = ENVS_DIR / "mamba_root"
# linux-64 static build; the VM platform. (Detect arch later if we ever run
# elsewhere — out of scope for the single-VM prototype.)
_MAMBA_URL = "https://micro.mamba.pm/api/micromamba/linux-64/latest"


def ensure_micromamba() -> str:
    """Return the path to a usable micromamba binary, downloading it on first
    use. Idempotent."""
    if MAMBA_BIN.exists() and os.access(MAMBA_BIN, os.X_OK):
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
    return env


def run_micromamba(args: Sequence[str], *, timeout_s: int = 1800) -> subprocess.CompletedProcess:
    """Run micromamba with the standalone root prefix set. Raises on failure."""
    mamba = ensure_micromamba()
    proc = subprocess.run([mamba, *args], capture_output=True, text=True,
                          env=_mamba_env(), timeout=timeout_s)
    if proc.returncode != 0:
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
