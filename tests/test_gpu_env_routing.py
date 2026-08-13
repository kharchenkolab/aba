"""GPU env routing: a GPU job rides the site's declared CUDA pack — and NOTHING
else changes.

The design premise (docs/arch/envs.md, GPU/accelerator): the CUDA science stack
is ~5x the disk of the CPU base, and on this class of site only Slurm *jobs* can
reach the GPU node — so the deployment publishes a CUDA *flavour* of the base
(derived mechanically, scripts/derive_gpu_pack.py) that GPU-estimated background
jobs ride, while every interactive session keeps the 676 MB CPU base and macOS
keeps its default Metal/MPS build with nothing declared at all.

What is load-bearing here, in order:

1. **The default is untouched.** With no `jobs.gpu_env_pack` declared, the
   feature must not merely be OFF — it must be ABSENT: no bundle lookup, no
   config branch a future refactor can widen. Asserted by making the bundle
   EXPLODE on contact.
2. **A non-GPU job never gets the CUDA env** (the other side — 3.4 GB and a
   B200-adjacent identity for a pandas groupby would be the quiet failure).
3. **Misconfiguration refuses instead of degrading.** A declared-but-missing
   pack must stop the submit: the fallback would be the project snapshot,
   whose torch is the CPU build — a GPU job silently riding it is the
   scVI-on-CPU incident wearing a config typo.
4. **Both lanes consult the ONE rule** (`_gpu_env_for`). The two submit lanes
   drifting apart over when a job leaves the project env is how the same
   request behaves differently detached vs shared.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.compute import base_env, env_packs  # noqa: E402
from core.compute.errors import ComputeError  # noqa: E402
from core.jobs import weft_submitter as ws  # noqa: E402

pytestmark = pytest.mark.platform

ENVVAR = "ABA_JOBS_GPU_ENV_PACK"
PACK = "python-bio-cuda"
EID = "env:v1:" + "c" * 64


def _boom(*_a, **_k):
    raise AssertionError("the bundle must not be consulted on this path")


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    """gpu_pack_env_id caches (name, digest) → EnvID for the process lifetime;
    tests must not leak identities into each other through it."""
    monkeypatch.setattr(base_env, "_env_ids", {})


# ── 1. the default is ABSENT, not merely off ────────────────────────────────

def test_unset_touches_nothing(monkeypatch):
    monkeypatch.delenv(ENVVAR, raising=False)
    monkeypatch.setattr(env_packs, "pack_spec", _boom)
    gpu_job = {"estimate": {"gpu": True}}
    assert ws._gpu_env_for(gpu_job, "python") is None
    assert base_env.gpu_pack_env_id() is None


# ── 2. the routing rule, all four gates ─────────────────────────────────────

def test_gpu_python_job_rides_the_declared_pack(monkeypatch):
    monkeypatch.setenv(ENVVAR, PACK)
    monkeypatch.setattr(env_packs, "pack_spec",
                        lambda n: {"deps": {}} if n == PACK else None)
    from core.compute import seeding
    monkeypatch.setattr(seeding, "adopt_env_id",
                        lambda n: EID if n == PACK else None)
    assert ws._gpu_env_for({"estimate": {"gpu": True}}, "python") == EID


def test_non_gpu_job_never_gets_the_gpu_env(monkeypatch):
    """The other side. Even with the site fully configured, a job that did not
    ask for a GPU must not touch the pack — not resolve it, not look it up."""
    monkeypatch.setenv(ENVVAR, PACK)
    monkeypatch.setattr(env_packs, "pack_spec", _boom)
    assert ws._gpu_env_for({"estimate": {"cores": 4}}, "python") is None
    assert ws._gpu_env_for({}, "python") is None


def test_explicit_env_wins_over_gpu(monkeypatch):
    """env= is the caller's choice; the GPU rule never second-guesses it."""
    monkeypatch.setenv(ENVVAR, PACK)
    monkeypatch.setattr(env_packs, "pack_spec", _boom)
    assert ws._gpu_env_for({"estimate": {"gpu": True}, "env": "myenv"},
                           "python") is None


def test_r_gpu_job_keeps_its_normal_env(monkeypatch):
    """The pack is a python stack; an R job declaring gpu keeps its env."""
    monkeypatch.setenv(ENVVAR, PACK)
    monkeypatch.setattr(env_packs, "pack_spec", _boom)
    assert ws._gpu_env_for({"estimate": {"gpu": True}}, "r") is None


# ── 3. misconfiguration refuses, with the fix named ─────────────────────────

def test_declared_but_missing_pack_refuses(monkeypatch):
    monkeypatch.setenv(ENVVAR, PACK)
    monkeypatch.setattr(env_packs, "pack_spec", lambda n: None)
    with pytest.raises(ComputeError) as ei:
        base_env.gpu_pack_env_id()
    assert ei.value.code == "gpu_env_pack.unknown"
    # the message must name the pack and the fix, or the operator is stuck
    assert PACK in str(ei.value)
    assert "fix" in (ei.value.hints or {})


def test_solve_fallback_when_no_catalog(monkeypatch):
    """Adopt miss → private solve, same degradation ladder as env_id()."""
    monkeypatch.setenv(ENVVAR, PACK)
    monkeypatch.setattr(env_packs, "pack_spec", lambda n: {"deps": {"conda": ["x"]}})
    from core.compute import seeding
    monkeypatch.setattr(seeding, "adopt_env_id", lambda n: None)

    class _Stub:
        def env_ensure(self, spec, **kw):
            assert spec == {"deps": {"conda": ["x"]}}
            return {"env_id": EID}
    monkeypatch.setattr(base_env._adapter, "get_compute", lambda: _Stub())
    monkeypatch.setattr(base_env.named_envs, "_sync", lambda x: x)
    assert base_env.gpu_pack_env_id() == EID


# ── 4. both lanes consult the one rule ──────────────────────────────────────

def test_both_submit_lanes_consult_the_one_rule():
    """Structural pin: every submit lane that resolves a job's env identity
    calls `_gpu_env_for`. Two lanes with private copies of the rule is how a
    detached GPU job and a shared-lane GPU job start behaving differently."""
    tree = ast.parse((ROOT / "backend/core/jobs/weft_submitter.py").read_text())
    callers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "_gpu_env_for"):
                    callers.add(node.name)
    callers.discard("_gpu_env_for")
    assert "_detached_env" in callers, "the detached lane lost the GPU rule"
    assert len(callers) >= 2, (
        f"only {sorted(callers)} consult _gpu_env_for — the shared lane's env "
        f"fork no longer routes GPU jobs through the one rule")


def test_the_spec_stamp_names_the_trade():
    """The shared lane stamps env_source='gpu_pack:<name>' into the job spec.
    That stamp is what makes a missing project package in a GPU job read as
    'this ran the shared CUDA pack' rather than a mystery ImportError."""
    src = (ROOT / "backend/core/jobs/weft_submitter.py").read_text()
    assert '"env_source": env_source' in src
    assert 'f"gpu_pack:' in src


# ── derivation: one source spec, two flavours ───────────────────────────────

def _derive_mod():
    spec = importlib.util.spec_from_file_location(
        "aba_derive_gpu_pack", ROOT / "scripts" / "derive_gpu_pack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SRC_PACK = {
    "name": "python-bio", "title": "Single-cell Python",
    "languages": ["python"], "default_state": "on", "role": "base",
    "first_use": ["scanpy"],
    "import_names": {"scvi": "scvi-tools"},
    "spec": {"platforms": ["linux-64", "osx-arm64"],
             "deps": {"conda": ["python =3.12", "scvi-tools >=1.1"],
                      "pypi": ["lstar-sc ==0.2.2"]}},
}


def test_derived_flavour_shares_the_source_deps_exactly():
    """THE one-source guard: shared deps byte-identical, GPU additions only in
    the linux-64 variant. Drift here means the same analysis resolves different
    package versions depending on which lane ran it."""
    d = _derive_mod().derive(SRC_PACK, "13.3")
    assert d["spec"]["deps"] == SRC_PACK["spec"]["deps"]
    assert d["spec"]["variants"] == {
        "linux-64": {"conda": ["pytorch-gpu", "cuda-version <=13.3"]}}
    assert d["spec"]["system_requirements"] == {"cuda": "13.3"}


def test_derived_flavour_cannot_collide_with_the_base():
    """role must NOT be 'base' (two base packs for python would make base
    resolution ambiguous) and nothing may materialize it outside the job lane."""
    d = _derive_mod().derive(SRC_PACK, "13.3")
    assert d["name"] == "python-bio-cuda"
    assert d["role"] == "gpu"
    assert d["default_state"] == "off"
    assert d["first_use"] == []


def test_derived_flavour_is_linux_only_and_mac_needs_nothing():
    """The CUDA flavour exists for cluster jobs; macOS accelerates via the
    UNMODIFIED default pack (Metal/MPS ships in the default osx-arm64 builds),
    so the source pack's platforms must stay untouched by derivation."""
    d = _derive_mod().derive(SRC_PACK, "13.3")
    assert d["spec"]["platforms"] == ["linux-64"]
    assert SRC_PACK["spec"]["platforms"] == ["linux-64", "osx-arm64"]  # source unmutated


def test_derivation_refuses_to_stack():
    m = _derive_mod()
    once = m.derive(SRC_PACK, "13.3")
    with pytest.raises(ValueError):
        m.derive(once, "13.3")          # a derived pack (role: gpu) refuses


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
