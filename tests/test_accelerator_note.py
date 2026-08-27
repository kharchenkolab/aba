"""A job that used PyTorch on CPU, on a cluster that has GPUs, says so.

THE SILENT FAILURE. 2026-08-27: a training job was requested, the agent did not
set est_gpu, the job ran on a CPU partition and reported plain success. Asked
matched got, so nothing in the placement path could detect it; the only thing
that knew better was the payload, which imported torch and found no CUDA. The
user gets a slow answer and no reason. That is worse than a crash.

These tests are mostly about NOT crying wolf. A note that fires on CPU-only
clusters, or on jobs that never touched torch, gets tuned out — and then the one
time it matters nobody reads it.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.platform

from core.jobs.runner import _accelerator_note  # noqa: E402


@pytest.fixture
def gpu_site(monkeypatch):
    from core.jobs import weft_submitter
    monkeypatch.setattr(weft_submitter, "_gpu_partition_for", lambda s: "g")


@pytest.fixture
def cpu_only_site(monkeypatch):
    from core.jobs import weft_submitter
    monkeypatch.setattr(weft_submitter, "_gpu_partition_for", lambda s: None)


def test_fires_when_torch_ran_on_cpu_and_no_gpu_was_asked_for(gpu_site):
    note = _accelerator_note({"site": "cluster", "estimate": {}},
                             {"accelerator": "torch:cuda=0"})
    assert note and "did not request an accelerator" in note
    assert "est_gpu=true" in note, "the note must say what to do differently"
    assert "partition 'g'" in note, "name the partition so the claim is checkable"


def test_silent_when_the_job_did_ask_for_a_gpu(gpu_site):
    """It asked and didn't get one — that's a PLACEMENT failure, and sbatch
    already refuses it loudly ('Requested node configuration is not
    available'). Two messages for one problem is noise."""
    assert _accelerator_note({"site": "cluster", "estimate": {"gpu": True}},
                             {"accelerator": "torch:cuda=0"}) is None


def test_silent_on_a_cpu_only_cluster(cpu_only_site):
    """WIDE — the common shape elsewhere. Running on CPU where there are no
    GPUs is not a finding, and saying so on every job would be pure noise."""
    assert _accelerator_note({"site": "cluster", "estimate": {}},
                             {"accelerator": "torch:cuda=0"}) is None


def test_silent_when_cuda_was_actually_available(gpu_site):
    assert _accelerator_note({"site": "cluster", "estimate": {}},
                             {"accelerator": "torch:cuda=1"}) is None


def test_silent_when_the_payload_never_touched_torch(gpu_site):
    """The overwhelming majority of jobs. ABSENT is the common shape."""
    for result in ({}, {"accelerator": ""}, {"accelerator": None}):
        assert _accelerator_note({"site": "cluster", "estimate": {}}, result) is None


def test_silent_for_local_jobs(gpu_site):
    """A local job runs in the session's own allocation; there is no placement
    decision to have made differently."""
    for site in (None, "", "local"):
        assert _accelerator_note({"site": site, "estimate": {}},
                                 {"accelerator": "torch:cuda=0"}) is None


def test_silent_when_the_probe_could_not_tell(gpu_site):
    """'torch:cuda=?' means the probe itself raised. Guessing from an unknown
    is how a guard starts producing confident nonsense."""
    assert _accelerator_note({"site": "cluster", "estimate": {}},
                             {"accelerator": "torch:cuda=?"}) is None


def test_an_unreachable_site_does_not_break_the_job(monkeypatch):
    """A note is never worth failing a finished job over."""
    from core.jobs import weft_submitter

    def boom(_s):
        raise RuntimeError("host down")
    monkeypatch.setattr(weft_submitter, "_gpu_partition_for", boom)
    assert _accelerator_note({"site": "cluster", "estimate": {}},
                             {"accelerator": "torch:cuda=0"}) is None
