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
