"""A job that used PyTorch on CPU says so — and says which of the three things
was true.

THE SILENT FAILURE. 2026-08-27: a training job was requested, the agent did not
set est_gpu, the job ran on a CPU partition and reported plain success. Asked
matched got, so nothing in the placement path could detect it; the only thing
that knew better was the payload, which imported torch and found no CUDA. The
user gets a slow answer and no reason. That is worse than a crash.

THE SILENCE THAT REPLACED IT. The first version keyed on
`_gpu_partition_for(site)` being truthy — a lookup that returned None both for
"this site has no GPU" and for "ABA could not ask". On 2026-08-28 the cluster
went configless, the partition probe stopped completing, the site reported no
capabilities, and so: the estimate had no reason to ask for a GPU, the job ran
on CPU, and this note stayed quiet because it could not find a GPU partition
either. The outage disabled its own alarm. `gpu_capability` answers three ways
now, and the case that matters most is the one that used to be invisible.

These tests are still mostly about NOT crying wolf. A note that fires on
CPU-only clusters, or on jobs that never touched torch, gets tuned out — and
then the one time it matters nobody reads it.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.platform

from core.jobs.runner import _accelerator_note  # noqa: E402

CPU_JOB = {"accelerator": "torch:cuda=0"}


def _capability(monkeypatch, answer):
    from core.jobs import weft_submitter
    monkeypatch.setattr(weft_submitter, "gpu_capability", lambda s: answer)


@pytest.fixture
def gpu_site(monkeypatch):
    """The site answered, and it has GPUs."""
    from core.jobs.weft_submitter import GPU_HAS
    _capability(monkeypatch, ("g", GPU_HAS, "partition 'g' on site 'cluster'"))


@pytest.fixture
def cpu_only_site(monkeypatch):
    """The site answered, and it has no GPUs."""
    from core.jobs.weft_submitter import GPU_NONE
    _capability(monkeypatch, (None, GPU_NONE, "site 'cluster' advertises no GPU partition"))


@pytest.fixture
def unprobed_site(monkeypatch):
    """The site did NOT answer — the shape the configless switch produced."""
    from core.jobs.weft_submitter import GPU_UNKNOWN
    _capability(monkeypatch, (None, GPU_UNKNOWN,
                              "site 'cluster' lists no scheduler partitions — "
                              "the partition probe did not complete"))


# ── has GPUs, wasn't asked ──────────────────────────────────────────────────

def test_fires_when_torch_ran_on_cpu_and_no_gpu_was_asked_for(gpu_site):
    note = _accelerator_note({"site": "cluster", "estimate": {}}, CPU_JOB)
    assert note and "did not request an accelerator" in note
    assert "est_gpu=true" in note, "the note must say what to do differently"
    assert "partition 'g'" in note, "name the partition so the claim is checkable"


# ── asked, and did not get it ───────────────────────────────────────────────

def test_speaks_when_the_job_asked_for_a_gpu_and_torch_found_none(gpu_site):
    """THE HALF THAT WAS NEVER BUILT. This case used to return None, on the
    argument that "it asked and didn't get one" is a placement failure sbatch
    already refuses loudly. That is true only when placement FAILS. A job placed
    on the GPU partition that still sees no CUDA device — a MIG slice that never
    attached, a driver the image cannot talk to, a gres the node did not hand
    over — is submitted, runs, and SUCCEEDS on CPU. Exactly the shape this
    function exists for, excluded by an assumption nobody tested."""
    note = _accelerator_note({"site": "cluster", "estimate": {"gpu": True}}, CPU_JOB)
    assert note, "a job that asked for a GPU and ran on CPU must not be silent"
    assert "ASKED" in note
    assert "not a placement refusal" in note, (
        "the note must distinguish itself from the sbatch refusal, or it reads "
        "as a duplicate of one")


def test_the_asked_note_names_where_it_was_placed(gpu_site):
    note = _accelerator_note({"site": "cluster", "estimate": {"gpu": True}}, CPU_JOB)
    assert "placed on g" in note


# ── could not ask: the case the outage made invisible ───────────────────────

def test_speaks_when_the_site_could_not_be_asked(unprobed_site):
    """THE POINT OF THE THREE-WAY ANSWER."""
    note = _accelerator_note({"site": "cluster", "estimate": {}}, CPU_JOB)
    assert note, (
        "a site ABA could not probe produced total silence: no GPU capability "
        "for the estimate to ask from, and no note either, because the note "
        "read 'could not tell' as 'has not got'")
    assert "could not determine" in note
    assert "probe did not complete" in note, "carry the reason, not just the verdict"


def test_unknown_and_none_do_not_produce_the_same_outcome(unprobed_site, monkeypatch):
    """ARMED. This is the whole property: if these two ever collapse back into
    one answer, every test above still passes and the alarm is off again."""
    from core.jobs.weft_submitter import GPU_NONE
    unknown = _accelerator_note({"site": "cluster", "estimate": {}}, CPU_JOB)
    _capability(monkeypatch, (None, GPU_NONE, "site 'cluster' advertises no GPU partition"))
    none = _accelerator_note({"site": "cluster", "estimate": {}}, CPU_JOB)
    assert unknown != none, (
        "'could not ask' and 'has no GPU' produced the same result — the "
        "conflation this note was rewritten to remove")
    assert none is None and unknown is not None


# ── not crying wolf ─────────────────────────────────────────────────────────

def test_silent_on_a_cpu_only_cluster(cpu_only_site):
    """WIDE — the common shape elsewhere. Running on CPU where there are
    demonstrably no GPUs is not a finding, and saying so on every job would be
    pure noise."""
    assert _accelerator_note({"site": "cluster", "estimate": {}}, CPU_JOB) is None


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
        assert _accelerator_note({"site": site, "estimate": {}}, CPU_JOB) is None


def test_silent_when_the_probe_could_not_tell(gpu_site):
    """'torch:cuda=?' means the payload's own probe raised. Guessing from an
    unknown is how a guard starts producing confident nonsense."""
    assert _accelerator_note({"site": "cluster", "estimate": {}},
                             {"accelerator": "torch:cuda=?"}) is None


def test_a_raising_lookup_does_not_break_the_job(monkeypatch):
    """A note is never worth failing a finished job over. `gpu_capability`
    catches its own errors and answers UNKNOWN, so this is the belt to that
    braces — it must hold even if the lookup itself blows up."""
    from core.jobs import weft_submitter

    def boom(_s):
        raise RuntimeError("host down")
    monkeypatch.setattr(weft_submitter, "gpu_capability", boom)
    assert _accelerator_note({"site": "cluster", "estimate": {}}, CPU_JOB) is None


# ── the gap between the two sides ───────────────────────────────────────────
#
# The note's logic was tested. The node's measurement was tested. Nothing tested
# that the measurement REACHES the note, and it did not: WeftSubmitter.poll
# builds its result from a whitelist of keys, `accelerator` was not among them,
# and every detached job arrived at the finaliser with the field missing. The
# note could not fire for any cluster job — the only jobs it exists for.

def test_poll_carries_the_node_s_accelerator_reading_to_the_finaliser():
    """A PROPERTY over the whitelist, not a call: whatever the node measures and
    the finaliser consumes must appear in the dict that connects them."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "backend" / "core" / "jobs"

    node_writes = "accelerator" in (root / "detached_entry.py").read_text()
    note_reads = 'result_obj.get("accelerator")' in (root / "runner.py").read_text()
    assert node_writes and note_reads, (
        "PRECONDITION: this test connects detached_entry (writer) to "
        "runner._accelerator_note (reader); one of them no longer uses the field")

    src = (root / "weft_submitter.py").read_text()
    m = re.search(r'res = \{"status": node\.get.*?\n\n', src, re.S)
    assert m, "could not locate poll()'s result assembly"
    assert 'node.get("accelerator")' in m.group(0) or \
           'node["accelerator"]' in m.group(0), (
        "poll() drops the node's `accelerator` reading. The note keys on it, so "
        "the CPU-on-a-GPU-cluster warning cannot fire for ANY detached job — "
        "which is every cluster job.")
