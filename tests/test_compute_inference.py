"""Settings→Compute proposal inference (misc/compute_settings.md §5.4) —
pure-function tests over fixture capability records. No weft, no network."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.platform

from core.compute.inference import (  # noqa: E402
    build_site_config, pick_working_root, propose, suggest_name)


def slurm_caps() -> dict:
    return {
        "schema": "capabilities:v2",
        "os": "linux", "arch": "x86_64", "hostname": "login1.vbc.ac.at",
        "cpus": 16, "mem_gb": 64, "glibc": "2.31", "internet": True,
        "module_system": True, "gpus": [], "cuda_driver": "",
        "scheduler": {"type": "slurm", "version": "23.02", "partitions": [
            {"name": "cpu", "max_walltime": "14-00:00:00", "max_walltime_s": 1209600,
             "cpus_per_node": 64, "mem_gb_per_node": 256, "nodes": 400,
             "available": True, "gres": [], "features": []},
            {"name": "gpu", "max_walltime": "2-00:00:00", "max_walltime_s": 172800,
             "cpus_per_node": 128, "mem_gb_per_node": 1024, "nodes": 11,
             "available": True,
             "gres": [{"type": "gpu", "model": "a100", "count": 8}],
             "features": ["a100"]},
            {"name": "down-part", "max_walltime": "1:00:00", "max_walltime_s": 3600,
             "cpus_per_node": 8, "mem_gb_per_node": 32, "nodes": 1,
             "available": False, "gres": [], "features": []},
        ]},
        "storage": {"weft_root": "/home/me/.weft", "free_gb": 40, "total_gb": 100,
                    "candidates": [
                        {"path": "/home/me", "writable": True, "free_gb": 40, "total_gb": 100},
                        {"path": "/tmp", "writable": True, "free_gb": 800, "total_gb": 900},
                        {"path": "/scratch/me", "writable": True, "free_gb": 4300, "total_gb": 9000},
                        {"path": "/scratch", "writable": False, "free_gb": 4300, "total_gb": 9000},
                    ]},
    }


def workstation_caps() -> dict:
    return {
        "schema": "capabilities:v2", "os": "linux", "arch": "x86_64",
        "hostname": "gpubox", "cpus": 64, "mem_gb": 256, "internet": True,
        "module_system": False,
        "gpus": [{"model": "RTX 6000", "count": 2}], "cuda_driver": "550.54",
        "scheduler": {"type": "none"},
        "storage": {"weft_root": "/home/me/.weft", "free_gb": 100, "total_gb": 500,
                    "candidates": [
                        {"path": "/home/me", "writable": True, "free_gb": 100, "total_gb": 500},
                        {"path": "/work/me", "writable": True, "free_gb": 2000, "total_gb": 4000},
                    ]},
    }


# ── name inference ───────────────────────────────────────────────────────────

def test_suggest_name_skips_role_labels():
    assert suggest_name("me@login2.vbc.ac.at") == "vbc"
    assert suggest_name("me@submit-1.cluster.edu") == "cluster"
    assert suggest_name("gpubox") == "gpubox"
    assert suggest_name("me@login.hpc.uni.edu") == "hpc"


def test_suggest_name_decollides():
    assert suggest_name("me@login.vbc.ac.at", known_names={"vbc"}) == "vbc-2"
    assert suggest_name("me@login.vbc.ac.at", known_names={"vbc", "vbc-2"}) == "vbc-3"


# ── working-root pick ────────────────────────────────────────────────────────

def test_working_root_cluster_prefers_scratch():
    w = pick_working_root(slurm_caps()["storage"], scheduler=True)
    assert w["root"] == "/scratch/me/.weft"
    assert w["free_gb"] == 4300
    # every plausible choice is offered, honestly labeled — never a silent guess
    roots = {o["root"]: o["kind"] for o in w["options"]}
    assert roots["/scratch/me/.weft"] == "scratch"
    assert roots["/home/me/.weft"] == "home"
    assert "/tmp/.weft" not in roots          # volatile never proposed


def test_working_root_plain_server_prefers_home():
    """A single-node server's home is typically the backed-up, durable place —
    no purge policy to dodge (the ada case)."""
    w = pick_working_root(workstation_caps()["storage"], scheduler=False)
    assert w["root"] == "/home/me/.weft" and w["kind"] == "home"
    assert "backed up" in w["reason"]


def test_working_root_tight_home_defers_to_roomy_mount():
    w = pick_working_root({"candidates": [
        {"path": "/home/me", "writable": True, "free_gb": 5},
        {"path": "/data/scratch", "writable": True, "free_gb": 900}]},
        scheduler=False)
    assert w["root"] == "/data/scratch/.weft"


def test_working_root_no_candidates_falls_back_to_home():
    w = pick_working_root({"candidates": [
        {"path": "/tmp", "writable": True, "free_gb": 999}]})
    assert w["root"] == "~/.weft"        # volatile /tmp never proposed


# ── the full proposal ────────────────────────────────────────────────────────

def test_slurm_proposal():
    p = propose(slurm_caps(), dest="me@login1.vbc.ac.at",
                shared_paths=["/groups/lab"], accounts=["lab-alloc"])
    assert p["kind"] == "slurm" and p["machine_type"] == "Slurm cluster"
    assert p["name"] == "vbc"
    assert "Slurm cluster (v23.02)" in p["headline"]
    assert p["use_for"] == ["interactive", "background", "gpu"]  # gpu partition seen
    assert p["contract"] == "shared-fs"
    assert p["long_term"] == [{"path": "/groups/lab", "stable": True}]
    assert p["account"] == "lab-alloc"           # exactly one → autofilled
    sel = {r["name"]: r["selected"] for r in p["partitions"]}
    assert sel == {"cpu": True, "gpu": True, "down-part": False}
    assert p["durable"] is False                 # scratch-rooted → not durable
    assert p["totals"]["nodes"] == 412 and p["totals"]["gpus"] == 88


def test_workstation_proposal():
    p = propose(workstation_caps(), dest="me@gpubox")
    assert p["kind"] == "ssh" and p["machine_type"] == "GPU workstation"
    assert "2× RTX 6000" in p["headline"]
    assert p["use_for"] == ["interactive", "background", "gpu"]
    assert p["contract"] == "detached"           # no verified shared path
    # non-scheduler machine with a roomy home → home (durable) wins over /work
    assert p["working"]["root"] == "/home/me/.weft"
    assert p["durable"] is True                  # home-kind pick → durable guess
    assert {o["root"] for o in p["working"]["options"]} == \
        {"/home/me/.weft", "/work/me/.weft"}
    assert p["account"] is None


def test_server_proposal_no_gpu_no_account_guess():
    caps = workstation_caps()
    caps["gpus"] = []
    p = propose(caps, dest="me@files.lab.edu", accounts=["a", "b"])
    assert p["machine_type"] == "remote server"
    assert p["use_for"] == ["interactive", "background"]
    assert p["account"] is None and p["accounts"] == ["a", "b"]  # ambiguous → ask


# ── proposal → weft config ───────────────────────────────────────────────────

def test_build_site_config_slurm():
    p = propose(slurm_caps(), dest="me@login1.vbc.ac.at",
                shared_paths=["/groups/lab"], accounts=["lab-alloc"])
    cfg = build_site_config(p, dest="me@login1.vbc.ac.at",
                            ssh_opts=["-o", "StrictHostKeyChecking=yes"],
                            pixi_source="/opt/pixi")
    assert cfg["host"] == "login1.vbc.ac.at" and cfg["user"] == "me"
    assert cfg["root"] == "/scratch/me/.weft"
    assert cfg["policy"]["storage"] == {"large": "/groups/lab",
                                        "scratch": "/scratch/me"}
    # only 2 of 3 partitions selected → allowlist written
    assert cfg["policy"]["partitions_allowed"] == ["cpu", "gpu"]
    assert cfg["scheduler"] == {"account": "lab-alloc"}
    assert cfg["pixi_source"] == "/opt/pixi"


def test_build_site_config_carries_policy_notes():
    p = propose(slurm_caps(), dest="me@login1.vbc.ac.at")
    p["notes"] = ["use only on nights, EU time", "  ", ""]
    cfg = build_site_config(p, dest="me@login1.vbc.ac.at")
    assert cfg["policy"]["notes"] == ["use only on nights, EU time"]


def test_build_site_config_all_partitions_means_no_allowlist():
    p = propose(slurm_caps(), dest="me@login1.vbc.ac.at")
    for r in p["partitions"]:
        r["selected"] = True
    cfg = build_site_config(p, dest="me@login1.vbc.ac.at")
    assert "partitions_allowed" not in cfg.get("policy", {})
