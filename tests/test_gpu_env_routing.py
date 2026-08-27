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
