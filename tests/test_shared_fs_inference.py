"""A visible path is not a shared-fs contract — the platform must match too.

The canary proves the deployment's data paths EXIST on the machine. The
shared-fs LANE promises something stronger: that the controller's own
interpreter, named by absolute path, will EXECUTE there
(`{sys.executable} -m core.jobs.slurm_entry`). A cross-OS mount satisfies the
first and breaks the second.

Live (2026-07-27): an OrbStack Linux VM mounts the mac's filesystem at the same
paths, so the canary saw `/Users/<me>/.aba` and inferred shared-fs. Every
background job then ran a macOS arm64 python on aarch64 Linux and died with
`ModuleNotFoundError: No module named 'core'`, exit 1 — surfaced to the agent as
a bare `job.nonzero_exit` with no traceback, so it could not diagnose it either.

Generalizes past OrbStack: any heterogeneous cluster whose shared filesystem
spans architectures hits the identical trap.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.compute.inference import propose  # noqa: E402

_NORM = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}
HOST_OS = platform.system().lower()
HOST_ARCH = _NORM.get(platform.machine().lower(), platform.machine().lower())
FOREIGN = ("linux" if HOST_OS != "linux" else "darwin")
OTHER_ARCH = "x86_64" if HOST_ARCH == "arm64" else "arm64"

CANARY = ["/Users/someone/.aba", "/Users/someone/.aba/weft"]


def _contract(os_name, arch, shared=CANARY):
    caps = {"os": os_name, "arch": arch, "cpus": 8, "mem_gb": 16,
            "scheduler": {"type": "none"}}
    return propose(caps, dest="host", shared_paths=shared)["contract"]


def test_same_platform_plus_canary_is_shared_fs():
    """CEILING: the real shared-fs deployment (same OS+arch, paths present) must
    KEEP its fast lane — over-applying the fix would push every cluster onto the
    payload-copy path."""
    assert _contract(HOST_OS, HOST_ARCH) == "shared-fs"


def test_cross_OS_mount_is_NOT_shared_fs():
    """THE live bug: paths visible, platform foreign."""
    assert _contract(FOREIGN, HOST_ARCH) == "detached"


def test_cross_ARCH_mount_is_NOT_shared_fs():
    """The heterogeneous-cluster shape: same OS, different architecture."""
    assert _contract(HOST_OS, OTHER_ARCH) == "detached"


def test_the_orbstack_case_verbatim():
    """aarch64 Linux VM mounting a mac's filesystem — what actually happened."""
    assert _contract("linux", "aarch64") == ("shared-fs" if HOST_OS == "linux"
                                             and HOST_ARCH == "arm64" else "detached")


def test_no_canary_is_detached_regardless_of_platform():
    """Unchanged behaviour: without shared paths there is no shared-fs claim to
    make, whatever the platform."""
    assert _contract(HOST_OS, HOST_ARCH, shared=[]) == "detached"


def test_unknown_platform_falls_back_to_detached():
    """WIDE — the degenerate probe: a machine that cannot say what it is must
    not be trusted with the lane that fails obscurely. Detached is correct
    everywhere and only costs a payload copy."""
    assert _contract("", "", CANARY) == "detached"
    assert _contract("linux", "", CANARY) == "detached"
    assert _contract(None, None, CANARY) == "detached"


def test_arch_aliases_are_normalized():
    """amd64/x86_64 and aarch64/arm64 name the same machines; a spelling
    difference must not silently demote a genuine shared-fs deployment."""
    alias = {"x86_64": "amd64", "arm64": "aarch64"}.get(HOST_ARCH)
    if not alias:
        pytest.skip(f"no alias for {HOST_ARCH}")
    assert _contract(HOST_OS, alias) == "shared-fs"
