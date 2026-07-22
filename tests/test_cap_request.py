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


@pytest.fixture(autouse=True)
def _clean_probe_memo():
    """The probe memo is identity-keyed and process-global by design; tests
    reuse fake identities, so isolate them from each other's verdicts."""
    from core.exec import verify as _v
    _v._PROBE_MEMO.clear()
    yield
    _v._PROBE_MEMO.clear()


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
    # topology-independent: the r-bio module toggle must not decide this test
    monkeypatch.setattr(d, "_r_module_block", lambda: None)
    monkeypatch.setattr("core.compute.base_env.require", lambda lang: None)
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


# ── stage C: compile — one grammar/eco composer ─────────────────────────────
# The env= dispatch flattened rich records to bare names (D3) and the extend
# path derived ecosystems by prefix heuristics with no override (D1/F3).
# compile_extend owns grammar + eco; the doors pass its output through.

def test_compile_extend_composes_the_github_grammar():
    from content.bio.tools.cap_request import CapRequest, compile_extend
    req = CapRequest(name="pkgx", language="r", source="github",
                     package="org/repo", subdir="R", ref="dev")
    specs, eco = compile_extend(req, {"archetype": "r_package"}, "r")
    assert specs == ["org/repo/R@dev"] and eco == "cran", (specs, eco)
    # degenerate shapes: no subdir; no ref; neither
    req2 = CapRequest(name="p", language="r", source="github", package="o/r")
    assert compile_extend(req2, {"archetype": "r_package"}, "r") == (["o/r"], "cran")


def test_compile_extend_routes_ecosystems_explicitly():
    from content.bio.tools.cap_request import CapRequest, compile_extend
    # conda-source R cap → the conda eco, conda spelling as given
    r = CapRequest(name="x", language="r", source="conda", package="r-x")
    assert compile_extend(r, {"archetype": "r_package"}, "r") == (["r-x"], "conda")
    # bioconductor → conda-first spelling in the isolated lane (binary, no
    # BiocManager, no writable prefix)
    b = CapRequest(name="SomePkg", language="r", source="bioconductor",
                   package="SomePkg")
    assert compile_extend(b, {"archetype": "r_package"}, "r") == \
        (["bioconductor-somepkg"], "conda")
    # plain registry names: R env → cran; python env → pypi
    p = CapRequest(name="X", language="r", source="cran", package="X")
    assert compile_extend(p, {"archetype": "r_package"}, "r") == (["X"], "cran")
    u = CapRequest(name="attrs", language="python")
    assert compile_extend(u, None, "python") == (["attrs"], "pypi")
    # pip-provisioned cap → its declared specs, pypi
    pip = CapRequest(name="attrs", language="python")
    assert compile_extend(pip, {"archetype": "library",
                                "provisioning": {"pip": ["attrs>=23"]}},
                          "python") == (["attrs>=23"], "pypi")
    # conda-provisioned cap → conda eco (F3's namesake-to-PyPI misroute)
    tool = CapRequest(name="sometool", language="python")
    assert compile_extend(tool, {"archetype": "library",
                                 "provisioning": {"conda": "sometool"}},
                          "python") == (["sometool"], "conda")
    # non-package capability → None (the dispatch's fall-through)
    assert compile_extend(CapRequest(name="m"), {"archetype": "mcp_server",
                                                 "provisioning": {}},
                          "python") is None


def test_env_door_extends_with_composed_spec_and_eco(monkeypatch):
    """D3's kill at the door: the github grammar reaches named_envs.extend,
    WITH an explicit eco — never a bare name into a prefix heuristic."""
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    seen: dict = {}
    monkeypatch.setattr("core.catalog.resolve_capability",
                        lambda n: {"name": "pkgx", "archetype": "r_package",
                                   "provisioning": {"r": {"source": "cran",
                                                          "package": "pkgx"}}})
    monkeypatch.setattr(ne, "resolve",
                        lambda pid, name: {"language": "r", "env_id": "e1"})

    def _extend(pid, name, pkgs, *, eco=None, **k):
        seen["pkgs"], seen["eco"] = list(pkgs), eco
        return {"env_id": "e2"}

    monkeypatch.setattr(ne, "extend", _extend)
    monkeypatch.setattr(ne, "run_in",
                        lambda *a, **k: {"ok": True, "stdout": "CAPQ=1.0",
                                         "stderr": "", "returncode": 0})
    monkeypatch.setattr(d, "_evict_env_kernels", lambda name: 0)
    monkeypatch.setattr(d, "_pointer_env", lambda pid, lang: None)
    out = d.ensure_capability(
        {"name": "pkgx", "env": "grow", "source": "github",
         "package": "org/repo", "subdir": "R", "ref": "dev"},
        {"thread_id": "t"})
    assert seen.get("pkgs") == ["org/repo/R@dev"], (
        f"github grammar flattened at the env= door again: {seen}")
    assert seen.get("eco") == "cran", seen


def test_env_door_conda_packages_pre_extend(monkeypatch):
    """The explicit conda-eco passthrough at the extend door (D1's sibling):
    conda_packages land via their own conda-eco extend before the main spec."""
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    calls: list = []
    monkeypatch.setattr(ne, "resolve",
                        lambda pid, name: {"language": "r", "env_id": "e1"})
    monkeypatch.setattr(ne, "extend",
                        lambda pid, name, pkgs, *, eco=None, **k:
                        calls.append((list(pkgs), eco)) or {"env_id": "e2"})
    monkeypatch.setattr(ne, "run_in",
                        lambda *a, **k: {"ok": True, "stdout": "CAPQ=1.0",
                                         "stderr": "", "returncode": 0})
    monkeypatch.setattr(d, "_evict_env_kernels", lambda name: 0)
    from content.bio.tools.cap_request import CapRequest
    req = CapRequest(name="X", language="r", conda_packages=["zlib"],
                     project="prj")
    d._extend_into_named_env("grow", ["X"], {"name": "X"}, req=req, eco="cran")
    assert (["zlib"], "conda") in calls, (
        f"conda_packages never reached a conda-eco extend: {calls}")
    assert (["X"], "cran") in calls, calls


def test_make_isolated_env_exposes_conda_packages(monkeypatch):
    """D1's tool-surface kill: the eco passthrough exists at named_envs.create
    but the agent could not reach it."""
    import inspect
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    from content.bio.mcp_servers.aba_core.tools import discovery as mcp_disc
    sig = None
    for _n, _f in inspect.getmembers(mcp_disc):
        pass
    src = inspect.getsource(mcp_disc)
    assert "conda_packages" in src.split("def make_isolated_env")[1].split("def ")[0], (
        "the MCP surface does not expose conda_packages")
    seen: dict = {}
    monkeypatch.setattr(ne, "create",
                        lambda pid, name, **k: seen.update(k) or
                        {"env_id": "e1", "status": "created"})
    d.make_isolated_env({"name": "e", "language": "r", "packages": [],
                         "conda_packages": ["zlib"]}, {"thread_id": "t"})
    assert seen.get("conda_packages") == ["zlib"], (
        f"conda_packages dropped between tool and named_envs.create: {seen}")


def test_probe_name_for_github_is_the_repo_tail_not_the_subdir():
    """A composed spec 'org/repo/R@dev' must not verify library('R') — the
    load name is the repo tail (or the explicit library=), pending V3's
    resolved names."""
    from content.bio.tools.discovery import _probe_target_name
    from content.bio.tools.cap_request import CapRequest
    req = CapRequest(name="pkgx", language="r", source="github",
                     package="org/repo", subdir="R", ref="dev")
    assert _probe_target_name(["org/repo/R@dev"], {"name": "pkgx"}, req) == "repo"
    # explicit library still wins
    req2 = CapRequest(name="pkgx", language="r", source="github",
                      package="org/repo", subdir="R", library="RealName")
    assert _probe_target_name(["org/repo/R@dev"], {}, req2) == "RealName"


# ── F-V3a: ranked mode replaces the R session cascade ───────────────────────
# The conda→cran try/except and the conda-name translation move below the
# API: one ranked call, dialects derived by the substrate, verify-in-loop,
# typed attempts back. Bioconductor (needs repos) and pre-verb substrates
# keep the legacy cascade.

class _RankedAdapter:
    def __init__(self, result=None):
        self.calls: list = []
        self._result = result or {
            "satisfied": True, "changed": True,
            "attempts": [{"lane": "conda", "outcome": "installed",
                          "seconds": 2.0, "mutations": ["prefix"],
                          "spelling": "r-pkgx"}],
            "verified": {"PkgX": {"status": "passed", "got": "2.1"}},
            "runtime": {"prefix": "/p"}, "session_id": "s1"}

    async def ensure_available(self, target, request, lanes=None,
                               verify=True, probe=False):
        self.calls.append({"target": target, "request": request,
                           "lanes": lanes, "verify": verify})
        return self._result


def test_ensure_ranked_calls_verb_and_records_what_happened(monkeypatch):
    ad = _RankedAdapter()
    from core.compute import project_env as pe
    monkeypatch.setattr(pe, "ensure",
                        lambda pid, lang: {"session_id": "s1",
                                           "runtime": {"prefix": "/p"}})
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    row = {"additions": [], "rev": 0}
    monkeypatch.setattr(pe, "get", lambda pid, lang: row)
    saved: list = []
    monkeypatch.setattr(pe, "_save_row", lambda pid, lang, r: saved.append(r))
    monkeypatch.setattr(pe, "_current_runtime", lambda sid: None)
    out = pe.ensure_ranked("p", "r", ["PkgX"], lanes=["conda", "cran"],
                           verify={"loads": ["PkgX"]})
    c = ad.calls[0]
    assert c["request"] == ["PkgX"] and c["lanes"] == ["conda", "cran"]
    assert c["verify"] == {"loads": ["PkgX"]}
    assert out.get("satisfied") is True
    # identity doctrine: record what HAPPENED — the winning lane's eco and
    # the spelling actually used, so a rebuild replays reality
    assert saved and saved[0]["additions"], "winning lane never recorded"
    add = saved[0]["additions"][0]
    assert add["eco"] == "conda" and add["specs"] == ["r-pkgx"], add


def test_ensure_ranked_precheck_hit_records_nothing(monkeypatch):
    ad = _RankedAdapter(result={"satisfied": True, "changed": False,
                                "attempts": [], "verified": {},
                                "runtime": None, "session_id": "s1"})
    from core.compute import project_env as pe
    monkeypatch.setattr(pe, "ensure",
                        lambda pid, lang: {"session_id": "s1",
                                           "runtime": {"prefix": "/p"}})
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    saved: list = []
    monkeypatch.setattr(pe, "get", lambda pid, lang: {"additions": [], "rev": 0})
    monkeypatch.setattr(pe, "_save_row", lambda *a: saved.append(a))
    monkeypatch.setattr(pe, "_current_runtime", lambda sid: None)
    pe.ensure_ranked("p", "r", ["PkgX"], lanes=["conda", "cran"],
                     verify={"loads": ["PkgX"]})
    assert not saved, "a pre-check short-circuit must not mint a revision"


def test_ensure_ranked_returns_none_on_pre_verb_substrate(monkeypatch):
    from core.compute import project_env as pe

    class _Old:                                  # no ensure_available at all
        pass

    monkeypatch.setattr(pe, "ensure",
                        lambda pid, lang: {"session_id": "s1",
                                           "runtime": {"prefix": "/p"}})
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Old())
    assert pe.ensure_ranked("p", "r", ["X"], lanes=["conda", "cran"]) is None


def test_r_lane_uses_ranked_with_registry_name(monkeypatch):
    """The consumer-side conda-name translation retires: the verb receives
    the REGISTRY spelling and derives lane dialects itself."""
    import content.bio.tools.discovery as d
    seen: dict = {}
    monkeypatch.setattr(d, "_r_version_in_session",
                        lambda *a, **k: None if not seen else "2.1")

    def _ranked(pid, lang, names, *, lanes, verify=None):
        seen.update(names=list(names), lanes=list(lanes), verify=verify)
        return {"satisfied": True, "changed": True, "attempts": [],
                "verified": {}, "runtime": None}

    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            ensure_ranked=_ranked,
            install=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("legacy conda leg ran despite ranked")),
            run_installer=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("fallback installer ran despite satisfied")))))
    res = d._ensure_r_via_session(
        {"name": "PkgX", "provisioning": {"r": {"source": "cran",
                                                "package": "PkgX"}}},
        {}, None, "PkgX")
    assert seen.get("names") == ["PkgX"], (
        f"verb got a translated name, not the registry spelling: {seen}")
    assert seen.get("lanes") == ["conda", "cran"]
    assert (seen.get("verify") or {}).get("loads") == ["PkgX"]
    assert res["status"] == "ready", res


def test_r_lane_falls_back_to_cascade_when_ranked_unavailable(monkeypatch):
    import content.bio.tools.discovery as d
    calls: list = []
    monkeypatch.setattr(d, "_r_version_in_session", lambda *a, **k: None)
    monkeypatch.setattr(d, "_cran_lane",
                        lambda *a, **k: calls.append("cran") or (True, None, {}))
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            ensure_ranked=lambda *a, **k: None,        # pre-verb substrate
            install=lambda *a, **k: calls.append("conda") or (_ for _ in ()).throw(
                RuntimeError("no conda build")),
            run_installer=lambda *a, **k: {"ok": True})))
    d._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "cran",
                                             "package": "p"}}},
        {}, None, "p")
    assert calls and calls[0] == "conda" and "cran" in calls, (
        f"legacy cascade order broken on pre-verb substrate: {calls}")


def test_r_lane_exhaustion_threads_attempts_and_gates_the_lecture(monkeypatch):
    """env.unavailable_in_lanes: attempts ride the result; the syslib remedy
    fires only when an attempt carries a build-class code."""
    import content.bio.tools.discovery as d
    from core.compute.errors import ComputeError
    _atts = [{"lane": "conda", "outcome": "failed",
              "error": {"error": "env.solve_failed", "retryable": True}},
             {"lane": "cran", "outcome": "failed",
              "error": {"error": "env.solve_conflict"}}]

    def _ranked(*a, **k):
        raise ComputeError("env.unavailable_in_lanes",
                           "no ranked lane could provide the request",
                           stage="realize", hints={"attempts": _atts})

    monkeypatch.setattr(d, "_r_version_in_session", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            ensure_ranked=_ranked,
            install=lambda *a, **k: {"ok": True},
            run_installer=lambda *a, **k: {"ok": True})))
    res = d._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "cran",
                                             "package": "p"}}},
        {}, None, "p")
    assert res["status"] == "error"
    assert res.get("attempts") == _atts, "hints.attempts dropped at render"
    assert "missing SYSTEM library" not in res["note"], (
        "resolution-class exhaustion got the build-stage lecture")


# ── stage E: render — typed attempts surfaced, one ready-set, playbook ──────

def test_ready_set_counts_all_genuine_ready_statuses():
    """M5: ready_isolated / provided_by_pack are genuine readiness — omitting
    them made batch results report 'partial' with a misleading note."""
    import inspect
    from content.bio.mcp_servers.aba_core.tools import discovery as mcp_disc
    src = inspect.getsource(mcp_disc)
    rs = src.split("_READY = ")[1].split("}")[0]
    for status in ("ready_isolated", "provided_by_pack"):
        assert status in rs, f"{status!r} missing from the ready-set"
    assert "deferred" not in rs, "deferred promises nothing — not ready"


def test_error_results_carry_typed_attempts_when_available(monkeypatch):
    """Stage E render: the substrate's typed attempt records ride the
    agent-facing error result — the agent reads structure, not fused prose."""
    discovery = _flow_r(monkeypatch,
                        lane_info={"code": "env.realize_failed",
                                   "attempts": [{"lane": "conda",
                                                 "outcome": "failed",
                                                 "error": {"error": "x"}}]})
    res = discovery._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "cran",
                                             "package": "p"}}},
        {}, None, "p")
    assert res["status"] == "error"
    assert res.get("attempts") and res["attempts"][0]["lane"] == "conda", (
        f"typed attempts dropped at the render boundary: {res.keys()}")


def _flow_r(monkeypatch, *, lane_info):
    import content.bio.tools.discovery as d
    monkeypatch.setattr(d, "_r_version_in_session", lambda *a, **k: None)
    monkeypatch.setattr(d, "_cran_lane",
                        lambda *a, **k: (False, "declined", lane_info))
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no conda")),
            run_installer=lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("installer failed")))))
    return d


def test_playbook_covers_the_typed_vocabulary():
    """Doc-side CODES doctrine: every typed class the render layer can tag
    has a playbook row — an untagged class is advice the agent never gets."""
    doc = (ROOT / "backend" / "system_bundle" / "rules" /
           "env_failures.md").read_text()
    for cls in ("retryable", "env.solve_conflict", "env.solve_failed",
                "env.realize_failed", "env.unavailable_in_lanes",
                "halted", "installed_unverified", "unknown",
                "session.cold_base", "task.invalid", "not loadable",
                "make_isolated_env", "set_active_env"):
        assert cls in doc, f"playbook missing a row/lever for {cls!r}"


# ── F-V2: tagged-mode verb adoption behind execute (env_refi2 §3.4) ─────────
# project_env.install is the ONE crossing for session-lane eco installs; the
# substrate's ensure_available (tagged mode) replaces session_install there:
# verify-first pre-check (~0.4s satisfied re-ensure) + record-gating below the
# API, envelope + attempts from below. Fallback: pre-verb substrates keep the
# session_install path byte-identically.

class _VerbAdapter:
    """Fake compute with the tagged verb; records what it was asked."""
    def __init__(self):
        self.calls: list = []

    async def ensure_available(self, target, request, lanes=None, verify=True,
                               probe=False):
        self.calls.append({"target": target, "request": request,
                           "verify": verify})
        return {"satisfied": True, "changed": True,
                "attempts": [{"lane": "cran", "outcome": "installed",
                              "seconds": 1.0, "mutations": ["rlib"],
                              "resolved": ["RealName"]}],
                "verified": {"X": {"status": "passed", "check": "loads",
                                   "got": "2.0"}},
                "runtime": {"prefix": "/p"}, "session_id": "s1"}


class _LegacyAdapter:
    """Pre-verb substrate: no ensure_available attribute at all."""
    def __init__(self):
        self.calls: list = []

    async def session_install(self, session_id, **kw):
        self.calls.append(kw)
        return {"ok": True, "runtime": {"prefix": "/p"}}


def _pe(monkeypatch, ad):
    from core.compute import project_env as pe
    monkeypatch.setattr(pe, "ensure",
                        lambda pid, lang: {"session_id": "s1",
                                           "runtime": {"prefix": "/p"}})
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    monkeypatch.setattr(pe, "get", lambda pid, lang: {"additions": [], "rev": 0})
    monkeypatch.setattr(pe, "_save_row", lambda *a: None)
    monkeypatch.setattr(pe, "_current_runtime", lambda sid: None)
    return pe


def test_install_routes_through_the_tagged_verb(monkeypatch):
    ad = _VerbAdapter()
    pe = _pe(monkeypatch, ad)
    out = pe.install("p", "r", ["X"], eco="cran", verify={"loads": ["X"]})
    assert ad.calls, "install never reached ensure_available"
    c = ad.calls[0]
    assert c["target"] == {"session": "s1"}
    assert c["request"] == {"cran": ["X"]}
    assert c["verify"] == {"loads": ["X"]}
    # envelope truth surfaces to callers: attempts/verified/resolved
    assert out.get("satisfied") is True and out.get("attempts")
    assert out.get("verified", {}).get("X", {}).get("status") == "passed"
    assert out.get("resolved") == ["RealName"], (
        "per-attempt resolved names must flatten to the top level — the "
        "github resolved-name adoption reads them there")


def test_install_without_verify_passes_none_not_true(monkeypatch):
    """Tagged-mode verify default is OFF for compat (converged design); an
    installless verify=True from us would flip semantics silently."""
    ad = _VerbAdapter()
    pe = _pe(monkeypatch, ad)
    pe.install("p", "python", ["a"], eco="pypi")
    assert ad.calls[0]["verify"] is None, ad.calls[0]


def test_install_falls_back_on_pre_verb_substrate(monkeypatch):
    ad = _LegacyAdapter()
    pe = _pe(monkeypatch, ad)
    out = pe.install("p", "r", ["X"], eco="cran", verify={"loads": ["X"]})
    assert ad.calls and ad.calls[0].get("cran") == ["X"], ad.calls
    assert ad.calls[0].get("verify") == {"loads": ["X"]}
    assert out.get("ok") is True


def test_install_with_repos_stays_on_session_install(monkeypatch):
    """cran_repos is not part of the verb's tagged request vocabulary yet —
    a repos-bearing install must keep the session_install path rather than
    silently dropping the repositories."""
    class _Both(_VerbAdapter):
        async def session_install(self, session_id, **kw):
            self.calls.append({"legacy": kw})
            return {"ok": True, "runtime": None}

    ad = _Both()
    pe = _pe(monkeypatch, ad)
    pe.install("p", "r", ["X"], eco="cran", cran_repos=["https://r.example"])
    assert ad.calls and "legacy" in ad.calls[0], (
        f"repos-bearing install took the verb and dropped repos: {ad.calls}")
    assert ad.calls[0]["legacy"].get("cran_repos") == ["https://r.example"]


def test_verb_envelope_is_checked_at_the_crossing(monkeypatch):
    """render-side adoption of check_envelope: a malformed envelope from the
    verb is flagged loudly at the ONE crossing (printed, not raised — the
    install itself succeeded)."""
    class _Bad(_VerbAdapter):
        async def ensure_available(self, *a, **k):
            return {"satisfied": True}          # missing required keys

    pe = _pe(monkeypatch, _Bad())
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = pe.install("p", "r", ["X"], eco="cran", verify={"loads": ["X"]})
    assert "envelope" in buf.getvalue().lower(), (
        "contract violation crossed silently")


# ── F-V1 remainder: verify blocks ride the env SPECS (weft P2) ──────────────
# The realize postcondition only arms if the spec carries the claim: a named
# env whose realization is broken on a new site must fail its OWN
# postcondition there (adopt default-on), not wait for a consumer probe.

def _cap_env_compute(monkeypatch):
    import core.compute.named_envs as ne
    import core.compute.adapter as ad
    seen: dict = {}

    class _C:
        async def env_ensure(self, spec):
            seen["spec"] = spec
            return {"env_id": "env_NEW", "status": "created"}

    monkeypatch.setattr(ad, "get_compute", lambda: _C())
    return seen


def test_extend_spec_carries_the_verify_block(monkeypatch, tmp_path):
    import core.config as cfg
    import core.compute.named_envs as ne
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    seen = _cap_env_compute(monkeypatch)
    ne.create("prjV", "e1", language="r", packages=[])
    seen.clear()
    ne.extend("prjV", "e1", ["X"], eco="cran",
              verify={"loads": ["X"], "versions": {"X": ">=2.0"}})
    assert seen["spec"].get("verify") == {"loads": ["X"],
                                          "versions": {"X": ">=2.0"}}, (
        f"extend dropped the claim — the realize postcondition never arms: "
        f"{seen['spec']}")
    # and the layer records it, so a platform re-lock replays the SAME claim
    row = ne.resolve("prjV", "e1")
    assert any(l.get("verify") == {"loads": ["X"], "versions": {"X": ">=2.0"}}
               for l in row.get("layers", [])), row.get("layers")


def test_create_spec_carries_the_verify_block(monkeypatch, tmp_path):
    import core.config as cfg
    import core.compute.named_envs as ne
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path)
    seen = _cap_env_compute(monkeypatch)
    ne.create("prjV", "e2", language="r", packages=[],
              verify={"loads": ["Y"]})
    assert seen["spec"].get("verify") == {"loads": ["Y"]}, seen["spec"]
    # absent → no verify key (never an empty-noise claim)
    seen.clear()
    ne.create("prjV", "e3", language="r", packages=[])
    assert "verify" not in seen["spec"], seen["spec"]


def test_capability_extend_door_writes_the_claim_onto_the_env(monkeypatch):
    """The capability path knows its load name precisely (library override /
    resolved names) — ITS claims go onto the env row, so the substrate
    enforces them at every future realization of that identity."""
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    seen: dict = {}
    monkeypatch.setattr("core.projects.current", lambda: "prjV")
    monkeypatch.setattr(ne, "resolve",
                        lambda pid, name: {"language": "r", "env_id": "e1"})
    monkeypatch.setattr(ne, "extend",
                        lambda pid, name, pkgs, **k: seen.update(k) or
                        {"env_id": "e2"})
    monkeypatch.setattr(ne, "run_in",
                        lambda *a, **k: {"ok": True, "stdout": "CAPQ=2.1",
                                         "stderr": "", "returncode": 0})
    monkeypatch.setattr(d, "_evict_env_kernels", lambda name: 0)
    from content.bio.tools.cap_request import CapRequest
    req = CapRequest(name="PkgX", language="r", min_version="2.0",
                     library="PkgX", project="prjV")
    d._extend_into_named_env("grow", ["PkgX"], {"name": "PkgX"},
                             req=req, eco="cran")
    assert seen.get("verify") == {"loads": ["PkgX"],
                                  "versions": {"PkgX": ">=2.0"}}, (
        f"the claim never reached the env spec: {seen}")


# ── stage D: target resolution parity + the r-base dedupe ───────────────────

def test_spec_for_rbase_caller_constraint_wins():
    """D2: the baked 'r-base =4.4.*' pin duplicated a caller-supplied r-base
    (weft now refuses dup specs at intake — truthful, but unactionable by the
    agent). The rule: a caller-CONSTRAINED r-base replaces the baked pin (a
    different R is the point of an isolated env — same as python_version); a
    bare 'r-base' dedupes away."""
    from core.compute.named_envs import _spec_for
    def _conda(spec):
        return spec["deps"]["conda"]
    # bare r-base → exactly one entry, the baked default
    c = _conda(_spec_for("p", "e", "r", [], None, ["r-base"]))
    assert [x for x in c if x.split()[0].split("=")[0] == "r-base"] == \
        ["r-base =4.4.*"], c
    # constrained caller pin REPLACES the baked default
    c = _conda(_spec_for("p", "e", "r", [], None, ["r-base =4.3.*"]))
    rb = [x for x in c if x.split()[0].split("=")[0] == "r-base"]
    assert rb == ["r-base =4.3.*"], c
    # packages-list r-base (prefix-routed) gets the same treatment
    c = _conda(_spec_for("p", "e", "r", ["r-base =4.2.*"], None, None))
    rb = [x for x in c if x.split()[0].split("=")[0] == "r-base"]
    assert rb == ["r-base =4.2.*"], c
    # no caller r-base → baked default, once; r-irkernel dedupe unaffected
    c = _conda(_spec_for("p", "e", "r", [], None, ["r-irkernel"]))
    assert c.count("r-irkernel") == 1
    assert [x for x in c if x.split()[0].split("=")[0] == "r-base"] == \
        ["r-base =4.4.*"], c


def test_pointer_and_explicit_env_produce_identical_plans(monkeypatch):
    """F2's dissolution, proven: the SAME rich request through the explicit
    env= door and through the promoted-pointer door reaches the substrate as
    the SAME plan (specs, eco, request fields). Promotion must never change
    what a request means."""
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    plans: list = []
    monkeypatch.setattr("core.catalog.resolve_capability",
                        lambda n: {"name": "pkgx", "archetype": "r_package",
                                   "provisioning": {"r": {"source": "cran",
                                                          "package": "pkgx"}}})
    monkeypatch.setattr(ne, "resolve",
                        lambda pid, name: {"language": "r", "env_id": "e1"})
    monkeypatch.setattr(ne, "extend",
                        lambda pid, name, pkgs, *, eco=None, **k:
                        plans.append((list(pkgs), eco)) or {"env_id": "e2"})
    monkeypatch.setattr(ne, "run_in",
                        lambda *a, **k: {"ok": True, "stdout": "CAPQ=2.5",
                                         "stderr": "", "returncode": 0})
    monkeypatch.setattr(d, "_evict_env_kernels", lambda name: 0)
    rich = {"name": "pkgx", "source": "github", "package": "org/repo",
            "subdir": "R", "ref": "dev", "min_version": "2.0"}
    # door 1: explicit env=
    monkeypatch.setattr(d, "_pointer_env", lambda pid, lang: None)
    out1 = d.ensure_capability({**rich, "env": "grow"}, {"thread_id": "t"})
    # door 2: promoted pointer, no env=
    monkeypatch.setattr(d, "_pointer_env", lambda pid, lang: ("grow", "r"))
    out2 = d.ensure_capability({**rich}, {"thread_id": "t"})
    assert plans[0] == plans[1], (
        f"promotion changed the request's meaning: {plans}")
    assert out1["status"] == out2["status"] == "ready"
    assert out1.get("requires") == out2.get("requires") == {
        "package": "repo", "min_version": "2.0"}


# ── stage B: verify — readiness is the REQUEST's postcondition ──────────────
# The named lane certified solves (D4/F4: ready-on-solve, cached=ready, dead
# verify_imports). Stage B: compose the claim from the request (weft's ONE
# grammar), pass it down the session verbs (weft V1), and probe the NAMED env
# consumer-side (until V3's env target) — cached answers included.

def test_verify_block_composes_weft_grammar():
    from content.bio.tools.cap_request import CapRequest, verify_block
    r = CapRequest(name="X", language="r", min_version="2.0")
    assert verify_block(r, libname="X") == {"loads": ["X"],
                                            "versions": {"X": ">=2.0"}}
    p = CapRequest(name="pkg", language="python")
    assert verify_block(p, import_name="pkg_mod") == {"import": ["pkg_mod"]}
    # no min_version → no versions key (weft refuses empty-noise keys)
    r2 = CapRequest(name="Y", language="r")
    assert verify_block(r2, libname="Y") == {"loads": ["Y"]}


def _extend_env(monkeypatch, *, extend_res, pre_id="e1",
                env_lang="r", req=None):
    """Drive _extend_into_named_env with named_envs stubbed (F-V3b: no
    consumer probe exists — run_in is TRAPPED to prove that)."""
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    seen: dict = {}
    monkeypatch.setattr("core.projects.current", lambda: "prjB")
    monkeypatch.setattr(ne, "resolve",
                        lambda pid, name: {"language": env_lang,
                                           "env_id": pre_id})

    def _extend(pid, name, pkgs, **k):
        seen["extend_kw"] = k
        if isinstance(extend_res, Exception):
            raise extend_res
        return extend_res

    monkeypatch.setattr(ne, "extend", _extend)
    monkeypatch.setattr(ne, "run_in",
                        lambda *a, **k: seen.__setitem__("probed", True) or
                        {"ok": True, "stdout": "", "stderr": "",
                         "returncode": 0})
    monkeypatch.setattr(d, "_evict_env_kernels", lambda name: 0)
    out = d._extend_into_named_env("grow", ["PkgX"], {"name": "PkgX"}, req=req)
    return seen, out


def test_extend_deferred_is_ready_with_honest_marker(monkeypatch):
    """F-V3b default (no verify-now available): the claim is recorded on the
    spec and enforced at every realization — ready, with a BRANCHABLE
    deferred marker and a note that says which enforcement happened. Never a
    fabricated 'verified'."""
    seen, out = _extend_env(monkeypatch, extend_res={"env_id": "e2"})
    assert out["status"] == "ready"
    assert out.get("verification") == "deferred", out
    assert not out.get("verified"), "verified fabricated without a live check"
    assert "realiz" in out["note"].lower(), (
        f"note must say enforcement is at realization: {out['note']!r}")
    assert seen.get("extend_kw", {}).get("verify"), (
        "ready-with-deferral is only honest if the CLAIM went down")
    assert not seen.get("probed"), "consumer probe ran — F-V3b deleted it"


def test_extend_verified_now_is_relayed(monkeypatch):
    """When the substrate proved the claim against a ready realization
    (site= verify-now), the result relays verified + site verbatim."""
    seen, out = _extend_env(monkeypatch, extend_res={
        "env_id": "e2",
        "verified": {"PkgX": {"status": "passed", "got": "2.1"}},
        "verified_site": "local"})
    assert out["status"] == "ready"
    assert out.get("verification") == "verified_now" and out.get("verified")
    assert "local" in out["note"] and "2.1" in out["note"], out["note"]
    assert not seen.get("probed")


def test_extend_failed_claim_is_an_error_not_ready(monkeypatch):
    """A ready realization that FAILS its own claim comes back typed
    (env.realize_failed + hints.postcondition) — a degraded-build finding."""
    from core.compute.errors import ComputeError
    err = ComputeError("env.realize_failed", "postcondition failed",
                       stage="realize",
                       hints={"postcondition": True, "env_id": "e2"})
    seen, out = _extend_env(monkeypatch, extend_res=err)
    assert out["status"] == "error"
    assert out.get("error", {}).get("error") == "env.realize_failed", out
    assert not seen.get("probed")


def test_extend_cached_keeps_honesty(monkeypatch):
    """F4 under enforce-at-realize: a cached answer is ready ONLY because the
    claim rides the spec — deferred marker, no fabricated verified, and no
    consumer probe resurrected for the no-op."""
    seen, out = _extend_env(monkeypatch,
                            extend_res={"env_id": "e1"})   # same id → cached
    assert out["status"] == "ready"
    assert out.get("verification") == "deferred" and not out.get("verified")
    assert not seen.get("probed")


def test_extend_ready_emits_requires_and_floor_rides_the_claim(monkeypatch):
    """The version floor moved INTO the claim (versions: >=X) — the substrate
    enforces it wherever it verifies; the consumer stops re-deriving it."""
    from content.bio.tools.cap_request import CapRequest
    req = CapRequest(name="PkgX", language="r", min_version="2.0",
                     project="prjB")
    seen, out = _extend_env(monkeypatch, extend_res={"env_id": "e2"}, req=req)
    assert out["status"] == "ready"
    assert out.get("requires") == {"package": "PkgX", "min_version": "2.0"}
    vb = seen.get("extend_kw", {}).get("verify") or {}
    assert vb.get("versions", {}).get("PkgX") == ">=2.0", (
        f"the floor never reached the claim: {vb}")


def test_extend_note_names_the_env_language_lane(monkeypatch):
    """D4's note half: an R env's success note must say run_r, not
    run_python."""
    seen, out = _extend_env(monkeypatch, extend_res={"env_id": "e2"},
                            env_lang="r")
    assert "run_r(" in out["note"] and "run_python(" not in out["note"], (
        f"R env advised through the python lane: {out['note']!r}")


def test_extend_routes_through_the_env_target_verb(monkeypatch):
    """named_envs.extend itself: with the verb available, the solve goes
    through target={'env': parent} with the claim as verify= — never a
    hand-built extends_env spec; pre-verb substrates keep env_ensure."""
    import core.compute.named_envs as ne
    import core.config as cfg
    import tempfile
    from pathlib import Path
    calls: dict = {}

    class _C:
        async def env_ensure(self, spec):
            return {"env_id": "env_BASE", "status": "created"}

        async def ensure_available(self, target, request, lanes=None,
                                   verify=True, probe=False):
            calls.update(target=target, request=request, verify=verify)
            return {"satisfied": True, "changed": True, "attempts": [],
                    "verified": {}, "runtime": None, "env_id": "env_NEW",
                    "note": "claim recorded; postconditions enforce at realize"}

    mp2 = tempfile.mkdtemp(prefix="aba_fv3b_")
    monkeypatch.setattr(cfg, "PROJECTS_DIR", Path(mp2))
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _C())
    ne.create("prjF", "e1", language="r", packages=[])
    calls.clear()
    res = ne.extend("prjF", "e1", ["X"], eco="cran",
                    verify={"loads": ["X"]})
    assert calls.get("target") == {"env": "env_BASE"}, calls
    assert calls.get("request") == {"cran": ["X"]}
    assert calls.get("verify") == {"loads": ["X"]}
    assert res["env_id"] == "env_NEW"
    assert "note" in res, "the enforcement note must survive to the door"


def test_session_cran_lane_passes_verify_down(monkeypatch):
    """weft V1: the session lanes carry the claim to the substrate — verify
    inside the install, record-gating below the API."""
    import content.bio.tools.discovery as d
    seen: dict = {}
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda pid, lang, specs, **k: seen.update(k) or {"ok": True},
            run_installer=lambda *a, **k: {"ok": True})))
    ok, err, info = d._cran_lane("p", "PkgX",
                                 verify={"loads": ["PkgX"]})
    assert ok and seen.get("verify") == {"loads": ["PkgX"]}, seen


def test_r_session_lane_composes_and_passes_the_claim(monkeypatch):
    """The R session door sends the request's claim down BOTH its lanes
    (conda and cran) — loads + version floor in weft's grammar."""
    import content.bio.tools.discovery as d
    calls: list = []
    monkeypatch.setattr(d, "_r_version_in_session", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda pid, lang, specs, **k: calls.append(("install", k))
                    or {"ok": True},
            run_installer=lambda *a, **k: calls.append(("installer", k))
                          or {"ok": True})))
    monkeypatch.setattr(d, "_r_version_in_session",
                        lambda *a, **k: None if not calls else "2.5")
    d._ensure_r_via_session(
        {"name": "PkgX", "provisioning": {"r": {"source": "cran",
                                                "package": "PkgX"}}},
        {"min_version": "2.0"}, None, "PkgX")
    conda_k = next(k for tag, k in calls if tag == "install")
    assert conda_k.get("verify") == {"loads": ["PkgX"],
                                     "versions": {"PkgX": ">=2.0"}}, calls


def test_run_installer_threads_verify_with_fallback(monkeypatch):
    """project_env.run_installer forwards verify= to the substrate, and
    degrades for a pre-V1 substrate exactly like writes_to does."""
    from core.compute import project_env as pe
    seen: dict = {}

    class _Ad:
        async def session_run_installer(self, sid, cmd, note="", **kw):
            if "old" in seen:                      # simulate pre-V1 substrate
                if "verify" in kw:
                    raise TypeError("unexpected keyword 'verify'")
            seen.update(kw)
            return {"ok": True}

    monkeypatch.setattr(pe, "ensure",
                        lambda pid, lang: {"session_id": "s1",
                                           "runtime": {"prefix": None}})
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Ad())
    monkeypatch.setattr(pe, "get", lambda pid, lang: {"additions": [], "rev": 0})
    monkeypatch.setattr(pe, "_save_row", lambda *a: None)
    monkeypatch.setattr(pe, "_current_runtime", lambda sid: None)
    pe.run_installer("p", "r", "cmd", writes_to="rlib",
                     verify={"loads": ["X"]})
    assert seen.get("verify") == {"loads": ["X"]}
    seen.clear(); seen["old"] = True
    pe.run_installer("p", "r", "cmd", writes_to="rlib",
                     verify={"loads": ["X"]})     # must not raise
    assert "verify" not in seen or seen.get("verify") is None


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
