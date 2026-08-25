"""A containerized deployment is host-less AND detached.

Live, 2026-08-25 (OOD, cbe-next). Once a slurm site was finally declared, jobs
reached the scheduler and then died on the node with

    cmd.sh: line 34: /opt/aba-venv/bin/python: No such file or directory   # 127

`site_contract` classified the site as **shared-fs** because it had no `host:`
— local transport on the submit node. That heuristic silently asserts the one
thing the shared-fs lane needs and the weft SIF profile does not provide: that
`{sys.executable} -m core.jobs.slurm_entry` will EXECUTE on a bare node. The
controller runtime is baked into the app image, so it will not.

This is the third appearance of the same invariant. `core/compute/inference.py`
already carries it in prose, after an OrbStack VM mounted a mac's filesystem at
identical paths and every job ran the mac's arm64 python on Linux:

    A VISIBLE PATH IS NOT A SHARED-FS CONTRACT.

The inference path tests it. The DECLARATION path guessed. These tests hold the
declaration path to the same standard, and pin the corollary the incident kept
obscuring: SIF re-entry is not the remedy. The detached lane already ships code
as data and runs the node's own interpreter under a weft-mounted env, which is
why weft was adopted; the fix is to select it, not to nest containers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

_HOSTLESS = [{"name": "cluster", "kind": "slurm",
              "config": {"root": "/scratch/u/weft"}}]


def _decl(monkeypatch, sites, aba=None):
    import core.compute.sites_config as sc
    monkeypatch.setattr(sc, "list_declared_sites", lambda: sites)
    monkeypatch.setattr(sc, "aba_keys", lambda n: dict(aba or {}))


def test_hostless_site_is_detached_when_the_controller_is_in_image(monkeypatch):
    """THE regression: in-image controller => detached, never shared-fs."""
    import core.jobs.weft_submitter as ws
    _decl(monkeypatch, _HOSTLESS)
    monkeypatch.setattr(ws, "controller_entry_reachable", lambda: False)
    assert ws.site_contract("cluster") == "detached"


def test_hostless_site_stays_shared_fs_on_a_native_install(monkeypatch):
    """WIDE: the fast path must survive for the deployments that earned it."""
    import core.jobs.weft_submitter as ws
    _decl(monkeypatch, _HOSTLESS)
    monkeypatch.setattr(ws, "controller_entry_reachable", lambda: True)
    assert ws.site_contract("cluster") == "shared-fs"


def test_explicit_contract_still_wins(monkeypatch):
    """WIDE: an operator who declares the contract is not second-guessed."""
    import core.jobs.weft_submitter as ws
    _decl(monkeypatch, _HOSTLESS, aba={"contract": "shared-fs"})
    monkeypatch.setattr(ws, "controller_entry_reachable", lambda: False)
    assert ws.site_contract("cluster") == "shared-fs"


def test_reachability_is_measured_not_assumed(monkeypatch):
    """The predicate must read the FILESYSTEM, not a path prefix or a setting.

    Both live incidents had a plausible-looking absolute path; only the mount
    fstype told them apart."""
    import core.exec.env_integrity as ei
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ei, "_classify_fs", lambda p: ("node_local", f"{p} squashfs"))
    assert ws.controller_entry_reachable() is False
    monkeypatch.setattr(ei, "_classify_fs", lambda p: ("shared", f"{p} nfs"))
    assert ws.controller_entry_reachable() is True
    # unknown is not a promise
    monkeypatch.setattr(ei, "_classify_fs", lambda p: ("unknown", "?"))
    assert ws.controller_entry_reachable() is False


def test_nextflow_head_stays_on_the_controller_when_the_site_is_detached(monkeypatch):
    """The detached harness runs ONE python/R script — it has no nextflow branch.

    Before this rule a nextflow job took the detached branch and was submitted
    as an EMPTY user_code.py: a job that succeeds having run nothing. The head
    is an orchestrator and fans out to Slurm through Nextflow's own executor,
    so the controller is where it belongs."""
    import core.jobs.submitter as sub
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    monkeypatch.setattr(ws, "site_contract", lambda s: "detached")

    assert sub._slurm_lane("run_nextflow").site == "local"
    assert sub._slurm_lane("run_python").site == "cluster"


def test_nextflow_head_still_offloads_on_a_shared_fs_site(monkeypatch):
    """WIDE: shared-fs deployments keep running the head on the node."""
    import core.jobs.submitter as sub
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    monkeypatch.setattr(ws, "site_contract", lambda s: "shared-fs")
    assert sub._slurm_lane("run_nextflow").site == "cluster"


def test_base_dir_check_is_quiet_when_the_cluster_site_is_detached(monkeypatch):
    """An in-image base is not a defect when nothing on the node names it.

    This branch used to report `high` and tell the operator to build a SIF
    re-entry wrap or move the base to shared FS — advice that contradicts the
    reason weft is here."""
    import core.exec.env_integrity as ei
    import core.jobs.submitter as sub
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(sub, "submitter_name", lambda: "slurm")
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    monkeypatch.setattr(ws, "site_contract", lambda s: "detached")
    monkeypatch.setenv("ABA_JOB_WRAP", "sif")
    monkeypatch.setattr(ei, "base_fs_kind", lambda: ("node_local", "in-image"))

    r = ei.check_base_dir_shared()
    assert r["ok"] is True, r
    assert "detached" in r["detail"].lower(), r["detail"]


def test_base_dir_check_still_fires_on_a_shared_fs_site_with_an_in_image_base(monkeypatch):
    """ARMED: the shared-fs lane with an unreachable base is still a defect."""
    import core.exec.env_integrity as ei
    import core.jobs.submitter as sub
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(sub, "submitter_name", lambda: "slurm")
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    monkeypatch.setattr(ws, "site_contract", lambda s: "shared-fs")
    monkeypatch.setenv("ABA_JOB_WRAP", "sif")
    monkeypatch.setattr(ei, "base_fs_kind", lambda: ("node_local", "in-image"))

    r = ei.check_base_dir_shared()
    assert r["ok"] is False and r["severity"] == "high", r
