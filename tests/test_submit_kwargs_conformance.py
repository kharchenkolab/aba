"""The kwargs one function BUILDS must be bindable by the function that RECEIVES them.

WHAT HAPPENED. `bg_submit_kwargs` (content/bio/tools/run_exec.py) builds the
kwargs for `submit_python_job`, and both call sites splat them:

    submit_python_job(code=…, **bg_submit_kwargs(input_, project_id))

A new key (`env_explicit`) was added to the producer and NOT to the callee's
signature. Every background Python submit then died at argument binding with
`TypeError: submit_python_job() got an unexpected keyword argument
'env_explicit'` — 4 of 4 in the first live session, GPU and CPU alike, before
any job row was created. The scheduler was never reached.

WHY THE EXISTING TESTS ALL PASSED. `tests/test_gpu_env_routing.py` calls
`bg_submit_kwargs` and asserts on the returned DICT; `tests/test_background_timeout.py`
asserts the source text contains `**bg_submit_kwargs(...)`. One checks the
producer, the other checks that the wiring is spelled correctly. Neither ever
puts the two together, so a binding error is invisible to both — the producer
and the consumer were each tested against my idea of the other.

THE PROPERTY, asserted here: whatever the producer emits, the callee can bind.
It needs no maintenance when a key is added — which is the point, because the
key that broke this was added by someone (me) who had just written tests for it.
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.platform


def _producer_kwargs(**overrides) -> dict:
    from content.bio.tools import run_exec
    from core.compute import named_envs
    real = named_envs.resolve_env
    try:
        named_envs.resolve_env = lambda pid, lang, explicit=None: explicit or None
        return run_exec.bg_submit_kwargs(
            {"estimated_runtime_min": 5, **overrides}, "prj_test")
    finally:
        named_envs.resolve_env = real


@pytest.mark.parametrize("overrides", [
    {},                                              # the bare shape
    {"env": "myenv"},                                # agent named an env
    {"est_gpu": True, "est_cores": 8, "est_mem_gb": 16},
    {"site": "cluster"},                             # detached lane
    {"execution": "slurm"},                          # forced submitter
])
def test_submit_python_job_can_bind_what_bg_submit_kwargs_produces(overrides):
    """Exactly the five shapes the live session used, all of which raised."""
    from core.jobs.submit import submit_python_job
    kw = _producer_kwargs(**overrides)
    sig = inspect.signature(submit_python_job)
    try:
        sig.bind(code="pass", title="t", focus_entity_id=None, **kw)
    except TypeError as e:
        pytest.fail(
            f"submit_python_job cannot accept what bg_submit_kwargs builds: {e}\n"
            f"producer keys: {sorted(kw)}\n"
            f"callee params: {sorted(sig.parameters)}\n"
            f"Every background submit fails at argument binding — before a job "
            f"row exists, so nothing appears in the jobs table or the logs.")


def test_the_producer_is_actually_producing_something():
    """ARMED. If bg_submit_kwargs returned {} the bind above would trivially
    succeed and this file would guard nothing."""
    kw = _producer_kwargs()
    assert kw, "bg_submit_kwargs returned nothing — the test above proves nothing"
    for required in ("estimate", "env", "timeout_s"):
        assert required in kw, f"producer no longer emits {required!r}"


def test_every_key_the_producer_emits_is_a_real_callee_parameter():
    """The other side: binding succeeds if the callee grows **kwargs, which
    would swallow a typo'd key silently instead of failing loudly."""
    from core.jobs.submit import submit_python_job
    params = inspect.signature(submit_python_job).parameters
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()), (
        "submit_python_job grew **kwargs — a misspelled producer key would now "
        "be accepted and silently dropped rather than raising")
    unknown = sorted(set(_producer_kwargs()) - set(params))
    assert not unknown, f"producer emits keys the callee does not declare: {unknown}"


def test_the_r_lane_keeps_the_same_shape():
    """submit_r_job does not use bg_submit_kwargs today (run_exec passes explicit
    keywords), which is exactly why it survived this defect. Assert the two
    signatures stay compatible anyway, so routing the R lane through the shared
    producer later cannot reintroduce it."""
    from core.jobs.submit import submit_python_job, submit_r_job
    py = set(inspect.signature(submit_python_job).parameters)
    r = set(inspect.signature(submit_r_job).parameters)
    assert not (py - r), f"submit_r_job is missing parameters python has: {sorted(py - r)}"


def test_env_explicit_reaches_the_consumer_through_params():
    """The flag is produced two layers above where it is read
    (weft_submitter._gpu_env_for). Binding is not enough — it has to travel in
    the job's params, which is a separate literal that was NOT updated when the
    signature was."""
    import inspect
    import re
    from pathlib import Path
    from core.jobs import submit as _submit
    src = (Path(__file__).resolve().parents[1] / "backend" / "core" / "jobs"
           / "submit.py").read_text()
    # Scope: the submit functions that DECLARE env_explicit. submit.py also
    # builds jobs of other kinds (run_nextflow, import_run) which have no such
    # parameter; demanding the flag there would be asserting a fiction.
    checked = 0
    for name in dir(_submit):
        fn = getattr(_submit, name)
        if not (name.startswith("submit_") and callable(fn)):
            continue
        if "env_explicit" not in inspect.signature(fn).parameters:
            continue
        m = re.search(rf"^def {re.escape(name)}\(.*?(?=\n(?:def |@|\Z))", src,
                      re.S | re.M)
        assert m, f"could not locate the body of {name}"
        assert '"env_explicit"' in m.group(0), (
            f"{name} accepts env_explicit but never puts it in the job's params "
            f"— the flag binds at the signature and is then dropped before "
            f"weft_submitter._gpu_env_for can read it, so GPU jobs silently "
            f"fall back to the project env")
        checked += 1
    assert checked >= 1, ("PRECONDITION: no submit function declares "
                          "env_explicit — this test is guarding nothing")


def test_the_critical_lane_group_names_real_scenarios():
    """`--lanes critical` is the gate's coverage CONTRACT. A typo'd or renamed
    member would silently shrink what the release is tested against — the same
    empty-subject-set failure as a lane that measures nothing, one level up."""
    import importlib.util
    from pathlib import Path
    wf = (Path(__file__).resolve().parents[1] / "regtest" / "live" / "workflows.py")
    spec = importlib.util.spec_from_file_location("_wf", wf)
    mod = importlib.util.module_from_spec(spec)
    import sys as _s
    _s.modules["_wf"] = mod
    spec.loader.exec_module(mod)
    known = {n for n, _ in mod.SCENARIOS}
    assert mod.GROUPS.get("critical"), "the critical group is empty or missing"
    for g, members in mod.GROUPS.items():
        unknown = [m for m in members if m not in known]
        assert not unknown, f"group {g!r} names non-existent scenarios: {unknown}"
    crit = set(mod.GROUPS["critical"])
    # The substrates that must be covered. Losing one of these is how a release
    # ships with, say, GPU submission untested.
    for must in ("wf_session_smoke", "wf_slurm_batch", "wf_gpu_recognised"):
        assert must in crit, f"critical no longer covers {must}"
