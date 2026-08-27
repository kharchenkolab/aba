"""Which env does a GPU-estimated background job actually run?

THE DEFECT THIS GUARDS. `_gpu_env_for` returns the site's declared GPU pack
only when the caller did not name an env of their own. But the params it reads
are built by `bg_submit_kwargs`, which ALWAYS populates `env` — from
`resolve_env`, which falls back to the project's ACTIVE named env when the agent
named nothing. So "the caller chose an env" was true for every project that had
one, the GPU pack was skipped, and a job submitted with `est_gpu=true` took a
GPU node and ran a stack with no CUDA in it. From the outside: "the GPU doesn't
work".

WHY IT SURVIVED A DIRECT TEST. It was checked once by calling
`_gpu_env_for({"estimate": {"gpu": True}}, "python")` — a dict with no `env` key
at all, which the background path never produces. The probe was more permissive
than reality and blessed the bug. So every case here builds its params through
`bg_submit_kwargs`, the function the product actually calls.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.platform

PACK = "sitepack-gpu"
GPU_ENV_ID = "env-gpu-0001"


@pytest.fixture
def routing(monkeypatch):
    """_gpu_env_for with the site declaring a GPU pack, and its EnvID resolvable."""
    from core.compute import base_env
    from core.jobs import weft_submitter
    # The setting is driven by its env var — Setting.get is read-only, and
    # patching the SETTING rather than its source would test a shape the
    # deployment never has.
    monkeypatch.setenv("ABA_JOBS_GPU_ENV_PACK", PACK)
    monkeypatch.setattr(base_env, "gpu_pack_env_id", lambda: GPU_ENV_ID)
    return weft_submitter._gpu_env_for


def _params(monkeypatch, *, est_gpu: bool, agent_env: str | None,
            project_env: str | None) -> dict:
    """The params the PRODUCT builds — through bg_submit_kwargs, so the shape
    (including a project-pointer `env`) is the real one."""
    from content.bio.tools import run_exec
    from core.compute import named_envs
    monkeypatch.setattr(
        named_envs, "resolve_env",
        lambda pid, lang, explicit=None: (explicit or project_env) or None)
    return run_exec.bg_submit_kwargs(
        {"est_gpu": est_gpu, "env": agent_env, "estimated_runtime_min": 5},
        "prj_test")


def test_gpu_job_in_a_project_with_an_active_env_still_gets_the_gpu_pack(
        routing, monkeypatch):
    """THE regression. The project has an env; the agent named none."""
    p = _params(monkeypatch, est_gpu=True, agent_env=None, project_env="myenv")
    assert p["env"] == "myenv", "precondition: the project pointer populated env"
    assert p["env_explicit"] is False, "precondition: the agent named nothing"
    assert routing(p, "python") == GPU_ENV_ID, (
        "a GPU job in a project that has an active env fell back to that env — "
        "it takes a GPU node and runs a stack with no CUDA in it")


def test_an_env_the_agent_actually_named_still_wins(routing, monkeypatch):
    """The rule the fix must NOT break: a deliberate `env=` outranks the pack,
    or `env=` would mean 'unless we know better'."""
    p = _params(monkeypatch, est_gpu=True, agent_env="myenv", project_env="other")
    assert p["env_explicit"] is True
    assert routing(p, "python") is None


def test_naming_the_pack_itself_selects_it(routing, monkeypatch):
    p = _params(monkeypatch, est_gpu=True, agent_env=PACK, project_env=None)
    assert routing(p, "python") == GPU_ENV_ID


def test_no_gpu_asked_means_no_pack(routing, monkeypatch):
    """The other side: the pack must not capture ordinary CPU jobs."""
    for project_env in (None, "myenv"):
        p = _params(monkeypatch, est_gpu=False, agent_env=None,
                    project_env=project_env)
        assert routing(p, "python") is None, f"project_env={project_env!r}"


def test_r_keeps_its_own_env(routing, monkeypatch):
    """The pack is a python stack; an R GPU job must not be diverted into it."""
    p = _params(monkeypatch, est_gpu=True, agent_env=None, project_env="myenv")
    assert routing(p, "r") is None


def test_a_site_with_no_gpu_pack_pays_nothing(monkeypatch):
    """Degenerate shape: no site declaration at all — the common case for most
    deployments, and it must not reach the bundle or divert anything."""
    from core.jobs import weft_submitter
    monkeypatch.delenv("ABA_JOBS_GPU_ENV_PACK", raising=False)
    p = _params(monkeypatch, est_gpu=True, agent_env=None, project_env="myenv")
    assert weft_submitter._gpu_env_for(p, "python") is None


def test_a_caller_predating_the_flag_keeps_the_old_reading(routing):
    """Params built by some other lane carry no env_explicit. Absent the flag a
    populated `env` must still read as deliberate — this fix changes ONE lane's
    meaning, and must not silently redirect callers it never examined."""
    assert routing({"env": "myenv", "estimate": {"gpu": True}}, "python") is None


# ── placement: a GPU ask must reach a partition that HAS GPUs ───────────────
#
# The live lane (regtest/live/workflows.py wf_gpu_recognised) proved the agent
# recognises a GPU workload on its own and sets estimate.gpu. The job was still
# refused, because nothing turned that ask into a PLACEMENT: weft's
# allowed_partition() returns partitions_allowed[0] when the site configures no
# partition, and never reads resources["gpus"]. The submitted script was
#     #SBATCH --gres=gpu:1
#     #SBATCH --partition=c        <- no GPUs on c
# and Slurm answered "Requested node configuration is not available".

CAPS = {
    "capabilities": {"scheduler": {"partitions": [
        {"name": "c", "nodes": 18, "gres": []},
        {"name": "g", "nodes": 1, "gres": [{"type": "gpu", "model": "b200", "count": 4}]},
    ]}},
    "config": {"policy": {"partitions_allowed": ["c", "g"]}},
}


@pytest.fixture
def describe(monkeypatch):
    """Patch the SITE DESCRIPTION, which is where the inventory really comes
    from — not the chooser, or the test would prove only that it agrees with
    itself."""
    from core.jobs import weft_submitter

    def _mk(desc):
        class _A:
            def sync_call(self, verb, *a, **k):
                assert verb == "sites_describe"
                return desc
        monkeypatch.setattr(weft_submitter, "_adapter", lambda: _A())
        return weft_submitter._gpu_partition_for
    return _mk


def test_gpu_ask_lands_on_a_partition_that_has_gpus(describe):
    assert describe(CAPS)("cluster") == "g"


def test_a_cpu_only_cluster_yields_no_partition(describe):
    """Absent: the common shape. No GPU partition means placement must be left
    exactly as it was, NOT forced to some arbitrary partition."""
    caps = {"capabilities": {"scheduler": {"partitions": [
        {"name": "c", "nodes": 18, "gres": []}]}}, "config": {}}
    assert describe(caps)("cluster") is None


def test_the_allowlist_is_never_widened(describe):
    """A GPU partition the user did not permit must not be chosen for them."""
    caps = {**CAPS, "config": {"policy": {"partitions_allowed": ["c"]}}}
    assert describe(caps)("cluster") is None


def test_the_roomiest_gpu_partition_wins(describe):
    caps = {"capabilities": {"scheduler": {"partitions": [
        {"name": "g-small", "nodes": 1, "gres": [{"type": "gpu", "count": 1}]},
        {"name": "g-big", "nodes": 4, "gres": [{"type": "gpu", "count": 8}]},
    ]}}, "config": {}}
    assert describe(caps)("cluster") == "g-big"


def test_a_site_that_cannot_describe_itself_is_not_fatal(monkeypatch):
    """Placement is best-effort: an unreachable site must leave the job alone,
    not raise on the submit path."""
    from core.jobs import weft_submitter

    class _A:
        def sync_call(self, *a, **k):
            raise RuntimeError("host down")
    monkeypatch.setattr(weft_submitter, "_adapter", lambda: _A())
    assert weft_submitter._gpu_partition_for("cluster") is None


def test_non_gpu_gres_is_not_mistaken_for_a_gpu(describe):
    """WIDE: a partition advertising some other gres (mps, fpga, licences) is
    not a GPU partition."""
    caps = {"capabilities": {"scheduler": {"partitions": [
        {"name": "x", "nodes": 2, "gres": [{"type": "fpga", "count": 4}]},
    ]}}, "config": {}}
    assert describe(caps)("cluster") is None


def test_every_gpu_resource_ask_also_sets_a_partition():
    """A PROPERTY guard over the submit path, not an instance check.

    The tests above exercise the chooser. Deleting the two lines that CALL it
    left every one of them green while the live bug returned — the classic
    "verified the output, not the forbidden action". Asking for a GPU without
    naming a partition is the defect, wherever it is written, so assert it over
    the file: each `resources["gpus"] = …` must be accompanied by a partition
    assignment in the same block. A third submit lane added later is covered
    without anyone remembering this test exists."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "backend" / "core" / "jobs"
           / "weft_submitter.py").read_text().splitlines()
    asks = [i for i, ln in enumerate(src) if re.match(r'\s*resources\["gpus"\]\s*=', ln)]
    assert asks, ("PRECONDITION: no `resources[\"gpus\"] =` found at all — this "
                  "test is reading the wrong file and proves nothing")
    for i in asks:
        window = "\n".join(src[i:i + 6])
        assert 'resources["partition"]' in window, (
            f"{'weft_submitter.py'}:{i + 1} asks Slurm for a GPU but never names a "
            f"partition. weft then defaults to partitions_allowed[0], which on a "
            f"mixed cluster is the CPU partition, and the job is refused with "
            f"'Requested node configuration is not available'.\n"
            + window)
