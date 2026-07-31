"""ComputeEnv: walltime parsing, the per-turn context line, and the live cluster
landscape read through the weft SitePort (Bucket 2 — the legacy slurm_live
introspection module was retired; partitions/load/access now come from the weft
site adapter)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.compute_env import slurm_time_to_min, _wait_label  # noqa: E402


def test_slurm_time_to_min():
    assert slurm_time_to_min("5-00:00:00") == 7200.0
    assert slurm_time_to_min("2:30:00") == 150.0
    assert slurm_time_to_min("45:00") == 45.0
    assert slurm_time_to_min("1-02:00:00") == 1560.0
    for bad in ("UNLIMITED", "INVALID", "", None, "NOT_SET"):
        assert slurm_time_to_min(bad) is None


def test_wait_label_from_live_load():
    """The weft-sourced wait signal: unavailable → idle → empty-queue → queued."""
    assert _wait_label(False, {}) == "unavailable"
    assert "quick" in _wait_label(True, {"cpus_idle": 8, "pending_jobs": 0})
    assert "moderate" in _wait_label(True, {"cpus_idle": 0, "pending_jobs": 0})
    assert "queued" in _wait_label(True, {"cpus_idle": 0, "pending_jobs": 3})


class _FakeAdapter:
    """Stands in for the weft SitePort — returns canned sites_describe /
    site_load / site_associations payloads (the real shapes)."""
    def __init__(self, describe, load, assoc):
        self._d, self._l, self._a = describe, load, assoc

    def sync_call(self, name, *a, **k):
        return {"sites_describe": self._d, "site_load": self._l,
                "site_associations": self._a}[name]


def test_cluster_landscape_maps_weft_payloads(monkeypatch):
    """_cluster_landscape maps weft's structured partitions + live load + assoc
    into the (partitions, user_access) shape context_line/describe_compute read."""
    import core.exec.compute_env as ce
    describe = {"capabilities": {"scheduler": {"partitions": [
        {"name": "normal", "cpus_per_node": 32, "mem_gb_per_node": 128,
         "max_walltime": "5-00:00:00", "available": True, "gres": []},
        {"name": "gpu", "cpus_per_node": 64, "mem_gb_per_node": 256,
         "max_walltime": "1-00:00:00", "available": True,
         "gres": [{"type": "gpu", "model": "a100", "count": 4}]}]}}}
    load = {"partitions": {
        "normal": {"cpus_idle": 32, "pending_jobs": 0},
        "gpu": {"cpus_idle": 0, "pending_jobs": 5}}}
    assoc = {"associations": [
        {"account": "lab", "partition": None, "allowed_qos": ["normal", "long"],
         "default_qos": "normal"}]}
    import core.compute as cc
    monkeypatch.setattr(cc, "get_compute",
                        lambda: _FakeAdapter(describe, load, assoc))
    parts, access = ce._cluster_landscape("cluster")
    pmap = {p["partition"]: p for p in parts}
    assert pmap["normal"]["gpu"] is False and pmap["gpu"]["gpu"] is True
    assert pmap["normal"]["cpus_per_node"] == 32
    assert "quick" in pmap["normal"]["wait"]        # idle CPUs → likely quick
    assert "queued" in pmap["gpu"]["wait"]          # no idle + pending → queued
    assert access == [{"account": "lab", "partition": None,
                       "qos": ["normal", "long"]}]


def test_context_line(monkeypatch):
    import core.exec.compute_env as ce
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {
        "mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
        "partitions": [{"partition": "gpu", "cpus_per_node": 32, "gpu": True, "wait": "likely quick"}]})
    line = ce.context_line()
    assert "slurm" in line and "8 cores / 32 GB" in line and "GPU" in line and "FRESH process" in line
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {
        "mode": "local", "node_cores": 4, "node_mem_gb": 16, "node_gpus": 0})
    l2 = ce.context_line()
    # The local branch used to read "background=True only on explicit request".
    # That wording went when the interactive ceiling replaced it; what the guard
    # was actually protecting is the substance — local mode names no scheduler,
    # and still tells the agent what governs backgrounding.
    assert "local" in l2 and "partitions" not in l2
    assert "background=True" in l2


def test_context_line_names_remote_machines(monkeypatch):
    """The per-turn cue names declared remote sites with the site= usage hint
    (this ambient mention is what makes the agent CONSIDER remote placement
    without a tool call) — and stays quiet when none are declared."""
    import core.exec.compute_env as ce
    base = {"mode": "local", "node_cores": 4, "node_mem_gb": 16, "node_gpus": 0}
    monkeypatch.setattr(ce, "compute_env",
                        lambda *a, **k: {**base, "remote_sites": ["hpc", "mendel"]})
    line = ce.context_line()
    assert "Remote machines available: hpc, mendel" in line
    assert "site=<name>" in line and "machine holding the inputs" in line
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: dict(base))
    assert "Remote machines" not in ce.context_line()   # quiescent when none


def test_context_line_gpu_usable(monkeypatch):
    """The per-turn cue tells the agent whether a GPU step will actually accelerate."""
    import core.exec.compute_env as ce
    base = {"mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
            "partitions": [{"partition": "gpu", "cpus_per_node": 32, "gpu": True, "wait": "idle"}]}
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {**base, "gpu_usable": True})
    assert "GPU usable" in ce.context_line()
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {
        **base, "gpu_usable": False, "gpu_usable_reason": "base torch is CPU-only — a GPU step would fall back to CPU"})
    warn = ce.context_line()
    assert "NOT usable" in warn and "CPU-only" in warn


# ── the cue is assembled by appending clauses, and two defects come from that ──

_SHAPES = {
    "slurm+parts+clock": {
        "mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
        "walltime_remaining_min": 48,
        "partitions": [{"partition": "c", "cpus_per_node": 192, "gpu": False, "wait": "likely quick"}]},
    "slurm+parts+noclock": {
        "mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
        "partitions": [{"partition": "c", "cpus_per_node": 192, "gpu": False, "wait": "likely quick"}]},
    "slurm+noparts+clock": {
        "mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
        "walltime_remaining_min": 48},
    "local+clock": {
        "mode": "local", "node_cores": 4, "node_mem_gb": 16, "node_gpus": 0,
        "walltime_remaining_min": 20},
    "local+plain": {
        "mode": "local", "node_cores": 4, "node_mem_gb": 16, "node_gpus": 0},
    "slurm+parts+clock+remote": {
        "mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
        "walltime_remaining_min": 48, "remote_sites": ["siteA"],
        "partitions": [{"partition": "c", "cpus_per_node": 192, "gpu": False, "wait": "likely quick"}]},
}


def _cue(monkeypatch, shape):
    import core.exec.compute_env as ce
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: dict(_SHAPES[shape]))
    return ce.context_line()


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_the_cue_never_renders_a_double_period(monkeypatch, shape):
    """PROPERTY, not an instance fix. Several clauses end in '.' and the next one
    prepends '. ', so any new clause added between them reintroduces '..'. Asserting
    over the assembled string means a future contributor fails without remembering."""
    line = _cue(monkeypatch, shape)
    assert ".." not in line, f"[{shape}] doubled punctuation in the per-turn cue: {line!r}"


@pytest.mark.parametrize("shape", [s for s in sorted(_SHAPES) if s.startswith("slurm")])
def test_the_interactive_ceiling_is_stated_whether_or_not_a_scheduler_exists(monkeypatch, shape):
    """The 30-min cap is a HARD property of the interactive lane, not a judgement
    that a scheduler can change. It used to render only on the no-partitions
    branch, so on a real cluster — the one case where a long step is at stake —
    the cue said 'weigh Slurm vs local' and never mentioned the limit that
    settles the question."""
    line = _cue(monkeypatch, shape)
    assert "30 min" in line, f"[{shape}] cue omits the interactive ceiling: {line!r}"
    assert "clamp" in line, f"[{shape}] cue does not say a larger timeout_s is clamped"
