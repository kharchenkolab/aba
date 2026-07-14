"""weft rewrite W1: the concrete bio env packs (tests/fixtures/installation/
envs/) compose through the loader and their `spec:` blocks are well-formed weft
EnvSpecs. This ties the facet machinery to real content — the packs that replace
install/core/{environment,r-environment}.yml under weft.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle.loader import _compose_envs, Provenance     # noqa: E402
from core.bundle.scope_resolver import ScopeBundle           # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "env-packs"


def _packs():
    scope = ScopeBundle(name="installation", label="Installation",
                        path=FIXTURE, present=True)
    return {p.name: p for p in _compose_envs([scope], Provenance())}


def test_bio_packs_present_and_shaped():
    packs = _packs()
    assert {"python-bio", "r-bio"} <= set(packs)
    py = packs["python-bio"].spec
    assert py["languages"] == ["python"]
    assert py["default_state"] == "on"
    deps = py["spec"]["deps"]
    assert any(d.startswith("scanpy") for d in deps["conda"])
    assert any(d.startswith("lstar-sc") for d in deps["pypi"])
    r = packs["r-bio"].spec
    assert r["default_state"] == "first_use"
    assert any(d.startswith("r-base") for d in r["spec"]["deps"]["conda"])


def test_specs_are_valid_weft_envspecs():
    """Every pack spec must be a shape weft's solver accepts: platforms is a
    list, deps is a dict of ecosystem→list. A malformed base is a content bug
    the composer should never mask."""
    for name, pack in _packs().items():
        spec = pack.spec.get("spec") or {}
        assert isinstance(spec.get("platforms", []), list), name
        deps = spec.get("deps") or {}
        assert isinstance(deps, dict) and deps, f"{name}: deps must be a non-empty dict"
        for eco, items in deps.items():
            assert eco in ("conda", "pypi", "cran", "julia"), f"{name}: bad ecosystem {eco}"
            assert isinstance(items, list) and items, f"{name}.{eco} must be a non-empty list"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("weft") is None,
    reason="weft package not installed")
def test_python_pack_dry_run_solves_shape():
    """A structural pass through weft's dry-run: it must ACCEPT the spec (parse
    + begin solve), not reject it as malformed. We don't require a full solve
    here (network/time) — only that weft doesn't return a spec-shape error."""
    from core.compute.adapter import resolve_pixi
    pixi = resolve_pixi()
    if pixi is None:
        pytest.skip("pixi binary not available")
    from weft.api import Weft
    import tempfile
    py = _packs()["python-bio"].spec["spec"]
    w = Weft(Path(tempfile.mkdtemp(prefix="aba_envpack_")), pixi_bin=pixi)
    r = w.env_ensure_dry_run(py)
    # Either a solved/plan result or a real solve outcome — but NOT a
    # spec-shape rejection (bad platforms/deps structure).
    if isinstance(r, dict) and "error" in r:
        assert r["error"] not in ("env.bad_spec", "env.invalid_spec"), r
