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


def _extend_env(monkeypatch, *, extend_res, probe, pre_id="e1",
                env_lang="r", req=None):
    """Drive _extend_into_named_env with named_envs stubbed."""
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    seen: dict = {}
    monkeypatch.setattr("core.projects.current", lambda: "prjB")
    monkeypatch.setattr(ne, "resolve",
                        lambda pid, name: {"language": env_lang,
                                           "env_id": pre_id})
    monkeypatch.setattr(ne, "extend", lambda pid, name, pkgs, **k: extend_res)

    def _run_in(pid, name, code, **k):
        seen["probe_env"] = name
        seen["probe_code"] = code
        return probe

    monkeypatch.setattr(ne, "run_in", _run_in)
    monkeypatch.setattr(d, "_evict_env_kernels", lambda name: 0)
    out = d._extend_into_named_env("grow", ["PkgX"], {"name": "PkgX"}, req=req)
    return seen, out


def test_extend_refuses_ready_when_the_package_does_not_load(monkeypatch):
    """D4's kill: a solve that minted an EnvID is not a loadable package."""
    seen, out = _extend_env(monkeypatch,
                            extend_res={"env_id": "e2"},
                            probe={"ok": True, "stdout": "CAPQ=MISSING",
                                   "stderr": "", "returncode": 0})
    assert out["status"] == "error", (
        f"extend certified a solve as ready with nothing loadable: {out}")
    assert "grow" in (out.get("note") or "")


def test_extend_cached_answers_must_also_pass_verify(monkeypatch):
    """F4's kill: 'already recorded' is a statement about a solve, not about
    what loads — the cached short-circuit may skip the solve, never the
    verification."""
    seen, out = _extend_env(monkeypatch,
                            extend_res={"env_id": "e1"},   # same id → cached
                            probe={"ok": True, "stdout": "CAPQ=MISSING",
                                   "stderr": "", "returncode": 0})
    assert out["status"] == "error", (
        f"cached extend returned ready for a spec that never loads: {out}")


def test_extend_probe_runs_in_the_target_env(monkeypatch):
    seen, out = _extend_env(monkeypatch,
                            extend_res={"env_id": "e2"},
                            probe={"ok": True, "stdout": "CAPQ=1.0",
                                   "stderr": "", "returncode": 0})
    assert seen.get("probe_env") == "grow", (
        "verification ran somewhere other than the env the user's code enters")
    assert out["status"] == "ready" and out.get("verified"), out


def test_extend_ready_emits_requires_and_enforces_min_version(monkeypatch):
    from content.bio.tools.cap_request import CapRequest
    req = CapRequest(name="PkgX", language="r", min_version="2.0",
                     project="prjB")
    # loadable but BELOW the floor → not ready (the request's postcondition)
    seen, out = _extend_env(monkeypatch, extend_res={"env_id": "e2"},
                            probe={"ok": True, "stdout": "CAPQ=1.4",
                                   "stderr": "", "returncode": 0}, req=req)
    assert out["status"] == "error" and "2.0" in out["note"], out
    # at the floor → ready, and the promised `requires` field exists at last
    seen, out = _extend_env(monkeypatch, extend_res={"env_id": "e2"},
                            probe={"ok": True, "stdout": "CAPQ=2.1",
                                   "stderr": "", "returncode": 0}, req=req)
    assert out["status"] == "ready"
    assert out.get("requires") == {"package": "PkgX", "min_version": "2.0"}


def test_extend_note_names_the_env_language_lane(monkeypatch):
    """D4's note half: an R env's success note must say run_r, not
    run_python."""
    seen, out = _extend_env(monkeypatch, extend_res={"env_id": "e2"},
                            probe={"ok": True, "stdout": "CAPQ=1.0",
                                   "stderr": "", "returncode": 0},
                            env_lang="r")
    assert "run_r(" in out["note"] and "run_python(" not in out["note"], (
        f"R env advised through the python lane: {out['note']!r}")


def test_probe_unknown_is_not_failed(monkeypatch):
    """The oracle contract (weft P0, mirrored consumer-side): a probe that
    COULD NOT RUN is unknown — refuse ready, but say the check failed to run,
    never that the package is absent."""
    seen, out = _extend_env(monkeypatch, extend_res={"env_id": "e2"},
                            probe={"ok": False, "stdout": "",
                                   "stderr": "site unreachable",
                                   "returncode": 1})
    assert out["status"] == "error"
    assert "could not run" in out["note"].lower() or "unknown" in out["note"].lower(), (
        f"an unrunnable check was reported as a package verdict: {out['note']!r}")


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
