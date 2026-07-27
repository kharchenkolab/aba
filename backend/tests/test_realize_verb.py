"""How aba forces an env to be REALIZED on a site.

weft `env_realize(env_id, site)` is the idempotent primitive for this: ready is a
no-op, missing/demoted/evicted rebuilds from the stored lock. Before it existed
(weft 01fb968) the only public lever was to run a TASK in the env — a "placebo"
whose command had to genuinely exercise the interpreter, because a bare `true`
resolves from the system PATH and materializes nothing (the E1 finding), and
which had to be submitted `force=True` because weft memoizes by
(command, env, inputs), so a repeated probe returned DONE from the FIRST
realization without rebuilding a since-evicted prefix.

These guard the switch to the verb. The load-bearing one is the typed error:
`ensure_ready` is what the kernel lane calls before `kernel_start`, and its
`env.platform_mismatch` is what triggers the lazy cross-platform re-lock. If that
code stops arriving, the kernel lane silently loses every cross-platform site
again (found live once already, on the aarch64 slurm fixture).
"""
import asyncio

import pytest

from core.compute import named_envs
from core.compute.errors import ComputeError


class _Ad:
    """A compute port. Port methods are COROUTINES (adapter._call is async), so
    these are too — a sync fake would not survive the asyncio.wait_for the
    timeout bound is built on, and would bless a version that dropped it."""

    def __init__(self, *, realize=None):
        self.calls: list = []
        self._realize = realize or (lambda: {"state": "ready"})

    async def env_realize(self, env_id, site):
        self.calls.append(("env_realize", env_id, site))
        return self._realize()

    async def task_submit(self, spec, force=False):
        self.calls.append(("task_submit", spec.get("command"), force))
        return {"job_id": "j1"}

    async def task_status(self, job_id):
        return [{"state": "DONE", "error": None}]

    async def env_status(self, env_id):
        return {}

    @property
    def verbs(self):
        return [c[0] for c in self.calls]


class _NoVerbAd(_Ad):
    """Older weft: the adapter raises AttributeError for an unknown tool."""

    def __getattribute__(self, name):
        if name == "env_realize":
            raise AttributeError("WeftAdapter: 'env_realize' is not a weft tool")
        return object.__getattribute__(self, name)


def _wire(monkeypatch, ad, *, ready_after=True):
    """Point named_envs at `ad`, run its coroutines for real, and make the env
    read NOT-ready before the realize and `ready_after` afterwards."""
    monkeypatch.setattr(named_envs._adapter, "get_compute", lambda: ad)
    monkeypatch.setattr(named_envs, "_sync", lambda coro: asyncio.run(coro))
    seen = {"n": 0}

    def _ready(env_id, site="local"):
        seen["n"] += 1
        return False if seen["n"] == 1 else ready_after

    monkeypatch.setattr(named_envs, "_realization_ready", _ready)
    return seen


def test_realize_uses_the_verb_and_submits_no_placebo_task(monkeypatch):
    ad = _Ad()
    seen = _wire(monkeypatch, ad)
    named_envs.ensure_ready("env:v1:x", site="hpc")
    # ARMED: a run where the env read READY up front proves nothing — the
    # realize path must actually have been entered.
    assert seen["n"] >= 2, "env was already ready; the realize path never ran"
    assert ("env_realize", "env:v1:x", "hpc") in ad.calls
    # THE load-bearing assertion (failure mode (a)): the point is to realize
    # WITHOUT running a task. Assert the forbidden action, not just the result.
    assert "task_submit" not in ad.verbs, f"placebo task still submitted: {ad.calls}"


def test_platform_mismatch_still_arrives_TYPED(monkeypatch):
    """The kernel lane's lazy re-lock keys on this exact code. A realize that
    fails must not be flattened into a generic env.realize_failed."""
    def _boom():
        raise ComputeError("env.platform_mismatch",
                           "env is locked for [linux-64] but site is linux-aarch64",
                           stage="realize", hints={"suggestion": "re-lock"})
    ad = _Ad(realize=_boom)
    _wire(monkeypatch, ad, ready_after=False)
    with pytest.raises(ComputeError) as ei:
        named_envs.ensure_ready("env:v1:x", site="hpc")
    assert ei.value.code == "env.platform_mismatch", \
        "typed code lost — the cross-platform kernel re-lock stops firing"
    assert ei.value.hints.get("suggestion") == "re-lock", "hints dropped"


def test_a_realize_that_never_returns_is_still_bounded(monkeypatch):
    """The task lane polled to a deadline. The verb blocks, so the bound has to
    be re-imposed — otherwise a hung realize hangs a kernel start forever."""
    async def _hang(self, env_id, site):   # patched onto the CLASS → bound
        await asyncio.sleep(30)
    ad = _Ad()
    monkeypatch.setattr(type(ad), "env_realize", _hang)
    _wire(monkeypatch, ad, ready_after=False)
    with pytest.raises(ComputeError) as ei:
        named_envs.ensure_ready("env:v1:x", timeout_s=0.05, site="hpc")
    assert ei.value.code == "env.realize_failed"


def test_older_substrate_falls_back_to_the_placebo_task(monkeypatch):
    """aba also runs against a weft older than the verb (cluster-personal
    installs). That must degrade to the task lane, not crash."""
    ad = _NoVerbAd()
    _wire(monkeypatch, ad)
    named_envs.ensure_ready("env:v1:x", site="hpc")
    assert "task_submit" in ad.verbs, "no fallback — old substrate would break"
    # and the fallback keeps what made it work: a command that EXERCISES the
    # env, forced past the memo so an evicted prefix actually rebuilds.
    cmd, force = [c[1:] for c in ad.calls if c[0] == "task_submit"][0]
    assert force is True, "memo not bypassed — an evicted prefix would not rebuild"
    assert cmd and cmd.strip() != "true", "placebo would resolve from system PATH"


def test_an_explicit_probe_still_RUNS(monkeypatch):
    """A caller that names a command is asserting THAT command must run in THIS
    env, and at least one leans on the side effect: `ensure_tool_env` probes the
    nextflow env with `nextflow -version`, and nextflow fetches its own
    distribution JARs on first invocation. Realizing without running it would
    silently move that download to the first real pipeline. The verb realizes;
    it does not run — so an explicit probe keeps the task lane."""
    ad = _Ad()
    _wire(monkeypatch, ad)
    named_envs.ensure_ready("env:v1:tool", probe="nextflow -version", site="hpc")
    assert "task_submit" in ad.verbs, "explicit probe was skipped — no warmup ran"
    assert "env_realize" not in ad.verbs, "verb bypassed the caller's probe"
    cmd, _force = [c[1:] for c in ad.calls if c[0] == "task_submit"][0]
    assert cmd == "nextflow -version", f"probe not honored: {cmd!r}"


def test_ready_env_realizes_nothing_at_all(monkeypatch):
    """WIDE, the degenerate shape: already realized → neither verb nor task."""
    ad = _Ad()
    monkeypatch.setattr(named_envs._adapter, "get_compute", lambda: ad)
    monkeypatch.setattr(named_envs, "_sync", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(named_envs, "_realization_ready",
                        lambda env_id, site="local": True)
    named_envs.ensure_ready("env:v1:x", site="hpc")
    assert ad.calls == [], f"realized an already-ready env: {ad.calls}"
