"""Stage A of env_refi2: the capability request is normalized ONCE.

`CapRequest` is the single place tool arguments and provisioning records are
read and merged; the doors (R session lane, env= dispatch) receive the request
object — so a field the agent sent can no longer evaporate at a door that never
learned about it (the F1 class: min_version/force honored only in the R session
lane; D3's flattening began with re-plucking raw inputs at the env= door).

Field-SURVIVAL is what stage A guards: the fields ARRIVE at every door.
Enforcement (verify, version recheck at the named lane) is stage B; grammar
composition is stage C. Behavior is otherwise frozen — these guards were
proven RED against pre-A code (doors received no request object at all).
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform

RICH_INPUT = {"name": "pkgx", "min_version": "2.0", "force": True,
              "library": "PkgX", "source": "github", "package": "org/repo",
              "subdir": "R", "ref": "dev"}


def _r_cap(name="pkgx"):
    return {"name": name, "archetype": "r_package",
            "provisioning": {"r": {"source": "cran", "package": name,
                                   "min_version": "1.5"}}}


# ── the constructor: one merge rule ─────────────────────────────────────────

def test_explicit_input_wins_over_the_record():
    from content.bio.tools.cap_request import build_cap_request
    req = build_cap_request(RICH_INPUT, _r_cap(), {"thread_id": "t"},
                            name="pkgx", language="r")
    assert req.source == "github" and req.package == "org/repo"
    assert req.subdir == "R" and req.ref == "dev"
    assert req.library == "PkgX"
    assert req.min_version == "2.0"          # input wins over record's 1.5
    assert req.force is True
    assert set(req.explicit_overrides) >= {"source", "package", "subdir",
                                           "ref", "library"}


def test_record_fields_survive_when_input_is_silent():
    from content.bio.tools.cap_request import build_cap_request
    req = build_cap_request({"name": "pkgx"}, _r_cap(), None,
                            name="pkgx", language="r")
    assert req.source == "cran" and req.package == "pkgx"
    assert req.min_version == "1.5"          # the RECORD's floor survives
    assert req.force is False and req.explicit_overrides == ()


def test_degenerate_shapes_absent_and_empty():
    from content.bio.tools.cap_request import build_cap_request
    # empty strings are ABSENT, not overrides (the '' → default rule)
    req = build_cap_request({"name": "x", "source": "", "min_version": "  "},
                            None, None, name="x", language=None)
    assert req.source == "cran"              # lane default preserved
    assert req.min_version is None
    assert req.package is None and req.language is None
    assert req.conda_packages == [] and req.repos == []


# ── language classification: one function, cli/conda is not python (M6) ─────

def test_classify_language_matches_the_entry_heuristic_where_it_was_right():
    from content.bio.tools.cap_request import classify_language
    assert classify_language(_r_cap()) == "r"
    assert classify_language({"archetype": "library",
                              "provisioning": {"pip": ["x"]}}) == "python"
    assert classify_language({"archetype": None, "provisioning": {}}) == "python"
    assert classify_language({"archetype": "mcp_server",
                              "provisioning": {}}) is None
    assert classify_language(None) is None


def test_conda_cli_capability_is_language_neutral():
    """M6: conda provisioning names an ARTIFACT, not a runtime. A cli tool
    with a conda spec was classified 'python', so ensure_capability(name,
    language='r') hit the mismatch reroute for a tool that has no language."""
    from content.bio.tools.cap_request import classify_language
    cli = {"archetype": "cli", "provisioning": {"conda": "sometool"}}
    assert classify_language(cli) is None, (
        "cli/conda capability classified as python — the M6 misroute")
    # but conda provisioning on a LIBRARY-ish cap still implies python
    assert classify_language({"archetype": "library",
                              "provisioning": {"conda": "libx"}}) == "python"


def test_entry_classification_is_owned_by_classify_language():
    """The inline heuristic must not survive as a private re-derivation."""
    import inspect
    import content.bio.tools.discovery as d
    src = inspect.getsource(d.ensure_capability)
    assert "classify_language" in src, (
        "ensure_capability no longer consults the one classification owner")


# ── field survival: the request ARRIVES at the doors ────────────────────────

def _entry(monkeypatch, input_, cap, *, env_row=None):
    """Drive ensure_capability with the catalog + doors stubbed, capturing
    what each door RECEIVES."""
    import content.bio.tools.discovery as d
    seen: dict = {}
    monkeypatch.setattr("core.catalog.resolve_capability", lambda n: cap)

    def _fake_r(cap_, input__, ctx_, name_, req=None, **k):
        seen["r_req"] = req
        return {"status": "ready", "name": name_, "archetype": "r_package",
                "library": name_, "note": "stub"}

    def _fake_extend(env_name, packages, cap_, req=None, **k):
        seen["extend_req"] = req
        seen["extend_pkgs"] = list(packages)
        return {"status": "ready", "name": (cap_ or {}).get("name"),
                "env": env_name, "env_id": "e2", "installed": list(packages),
                "note": "stub"}

    monkeypatch.setattr(d, "_ensure_r_via_session", _fake_r)
    monkeypatch.setattr(d, "_extend_into_named_env", _fake_extend)
    monkeypatch.setattr(d, "_pointer_env", lambda pid, lang: None)
    if env_row is not None:
        import core.compute.named_envs as _ne
        monkeypatch.setattr(_ne, "resolve", lambda pid, name: env_row)
    out = d.ensure_capability(dict(input_), {"thread_id": "t"})
    return seen, out


def test_r_session_door_receives_the_request(monkeypatch):
    seen, out = _entry(monkeypatch, {**RICH_INPUT, "language": "r"}, _r_cap())
    req = seen.get("r_req")
    assert req is not None, (
        "the R session door was called without the request object — "
        "fields can evaporate again (the F1 class)")
    assert req.min_version == "2.0" and req.ref == "dev"
    assert req.subdir == "R" and req.library == "PkgX"
    assert req.source == "github" and req.package == "org/repo"


def test_env_door_receives_the_request(monkeypatch):
    seen, out = _entry(monkeypatch, {**RICH_INPUT, "env": "grow"}, _r_cap(),
                       env_row={"language": "r", "env_id": "e1"})
    req = seen.get("extend_req")
    assert req is not None, (
        "the env= door was called without the request object — the D3/F1 "
        "flattening point is unguarded")
    assert req.min_version == "2.0" and req.ref == "dev" and req.subdir == "R"
    assert req.source == "github" and req.package == "org/repo"
    assert req.library == "PkgX"


def test_pointer_door_receives_the_same_request(monkeypatch):
    """F2's shape: no env= given, but the project has a promoted env — the
    request must arrive at the extend door as intact as the explicit-env one."""
    import content.bio.tools.discovery as d
    seen: dict = {}
    monkeypatch.setattr("core.catalog.resolve_capability", lambda n: _r_cap())

    def _fake_extend(env_name, packages, cap_, req=None, **k):
        seen["req"] = req
        return {"status": "ready", "name": "pkgx", "env": env_name,
                "env_id": "e2", "installed": list(packages), "note": "stub"}

    monkeypatch.setattr(d, "_extend_into_named_env", _fake_extend)
    monkeypatch.setattr(d, "_pointer_env", lambda pid, lang: ("hot", "r"))
    import core.compute.named_envs as _ne
    monkeypatch.setattr(_ne, "resolve",
                        lambda pid, name: {"language": "r", "env_id": "e1"})
    d.ensure_capability({**RICH_INPUT}, {"thread_id": "t"})
    req = seen.get("req")
    assert req is not None and req.min_version == "2.0" and req.ref == "dev", (
        f"pointer door dropped the request: {req}")


def test_direct_r_lane_call_builds_its_own_request(monkeypatch):
    """Compat: direct callers (env_check failure wing, existing tests) pass no
    req — the lane builds one from its inputs and behaves as before."""
    import content.bio.tools.discovery as d
    monkeypatch.setattr(d, "_r_version_in_session", lambda *a, **k: None)
    monkeypatch.setattr(d, "_cran_lane", lambda *a, **k: (False, "declined", {}))
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no conda")),
            run_installer=lambda *a, **k: {"ok": True})))
    res = d._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "cran", "package": "p"}}},
        {"min_version": "3.1"}, None, "p")
    assert res["status"] == "error"
    assert "3.1" not in (res.get("version") or ""), res
