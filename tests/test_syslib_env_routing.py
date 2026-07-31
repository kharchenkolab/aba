"""A missing SYSTEM library must be diagnosable AND escapable.

Live 2026-07-22 on an adopted read-only base (cbe-next), walking the real agent
path for an R package whose build needs a system library the base lacks:

    ensure_capability('RNetCDF', language='r')
      -> configure: error: netcdf.h was not compiled
      -> "R install into the project env failed: … session installer failed —
          command: Rscript -e 'install.packages("RNetCDF")' | log_tail:
          Error in contrib.url(repos, type): trying to use CRAN without
          setting a mirror …"

Four defects in that one result, each guarded here:
  1. the fallback installer sets no `repos=`, so it dies on mirror config and
     that RED HERRING leads the note, burying the real cause;
  2. a conda package name (`r-rnetcdf`) is retried through the CRAN lane, so
     the agent is told the package "is not available for this version of R" —
     a diagnosis about the wrong ecosystem;
  3. the note names no way forward — probed for cold_base/conda/isolated
     env/make_isolated_env/system librar/base pack, all absent — while the
     lane that DOES work was verified live (RNetCDF loads in an isolated R env;
     the solver pulls netcdf + udunits transitively);
  4. set_active_env filed an R env under "python" (resolve() does not filter by
     language), so get_active(pid,'python')=='ncenv' for an R env while bare
     run_r stayed on 'default' and could not load it.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform


# ── 1 + 2: the fallback lane ────────────────────────────────────────────────

def _capture_installer(monkeypatch, *, cran_lane_ok=False):
    """Run _ensure_r_via_session with both substrate lanes stubbed, returning
    what the fallback installer was actually asked to run."""
    from content.bio.tools import discovery
    seen: dict = {}
    monkeypatch.setattr(discovery, "_r_version_in_session", lambda *a, **k: None)
    monkeypatch.setattr(
        discovery, "_cran_lane",
        lambda pid, spec, **k: (seen.setdefault("cran_spec", spec),
                                (cran_lane_ok, None, {}))[1])

    def _run_installer(pid, lang, cmd, **k):
        seen["cmd"] = cmd
        return {"ok": True}

    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cold base")),
            run_installer=_run_installer)))
    return discovery, seen


@pytest.mark.parametrize("prov,expect_in_cmd", [
    ({"source": "cran", "package": "RNetCDF"}, 'install.packages("RNetCDF"'),
    ({"source": "github", "package": "org/repo"}, "install_github"),
    ({"source": "bioconductor", "package": "X"}, "BiocManager"),
])
def test_fallback_installer_sets_a_cran_mirror(monkeypatch, prov, expect_in_cmd):
    """`Rscript -e 'install.packages(...)'` with no repos= cannot work: a
    non-interactive R has no mirror and cannot prompt, so it dies with
    "trying to use CRAN without setting a mirror" — an error about mirror
    configuration that has nothing to do with why the install was needed."""
    discovery, seen = _capture_installer(monkeypatch)
    try:
        discovery._ensure_r_via_session(
            {"name": "p", "provisioning": {"r": prov}}, {}, None, "p")
    except Exception:  # noqa: BLE001 — post-install verification out of scope
        pass
    cmd = seen.get("cmd") or ""
    assert expect_in_cmd in cmd, f"wrong lane taken: {cmd!r}"
    assert "repos" in cmd, (
        f"fallback installer sets no CRAN mirror — it dies on contrib.url and "
        f"that error masks the real cause. cmd={cmd!r}")
    assert "http" in cmd, f"repos= present but names no mirror: {cmd!r}"


def test_conda_source_does_not_send_a_conda_name_to_cran(monkeypatch):
    """provisioning package under source='conda' is a CONDA name (r-rnetcdf).
    Handing it to a CRAN repo asks for something that cannot exist there and
    reports "'r-rnetcdf' is not available for this version of R" — the wrong
    ecosystem, with no hint that the conda lane is what refused."""
    discovery, seen = _capture_installer(monkeypatch, cran_lane_ok=True)
    discovery._ensure_r_via_session(
        {"name": "RNetCDF",
         "provisioning": {"r": {"source": "conda", "package": "r-rnetcdf",
                                "library": "RNetCDF"}}},
        {}, None, "RNetCDF")
    spec = seen.get("cran_spec")
    assert spec is not None, "cran lane never tried"
    assert not str(spec).startswith("r-"), (
        f"conda name {spec!r} sent to the CRAN lane — the agent gets a "
        f"'not available for this version of R' error about the wrong ecosystem")
    assert spec == "RNetCDF"


def test_github_fallback_keeps_the_subdir(monkeypatch):
    """The fallback took `_pkg` plus a separate ref=, silently DROPPING the
    subdir — the exact live failure the subdir support exists to survive,
    reintroduced on the path taken when the substrate lane declines."""
    discovery, seen = _capture_installer(monkeypatch)
    try:
        discovery._ensure_r_via_session(
            {"name": "p", "provisioning": {"r": {
                "source": "github", "package": "org/repo",
                "subdir": "R", "ref": "main"}}}, {}, None, "p")
    except Exception:  # noqa: BLE001
        pass
    cmd = seen.get("cmd") or ""
    assert "org/repo/R@main" in cmd, f"subdir/ref lost in the fallback: {cmd!r}"


# ── 3: the way out ──────────────────────────────────────────────────────────

_BUILD_FAIL = ("checking for netcdf.h... no\n"
               "configure: error: netcdf.h was not compiled\n"
               "ERROR: configuration failed")


def test_build_failure_names_the_lane_that_can_work(monkeypatch):
    """Diagnosis without a remedy still costs the turn: the live note carried
    the exact cause and named none of cold-base / isolated env / base pack, so
    the only signalled options were the two that cannot work."""
    from content.bio.tools import discovery
    from core.compute.errors import ComputeError
    monkeypatch.setattr(discovery, "_r_version_in_session", lambda *a, **k: None)
    monkeypatch.setattr(discovery, "_cran_lane",
                        lambda *a, **k: (False, None, {}))
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cold base")),
            run_installer=lambda *a, **k: (_ for _ in ()).throw(ComputeError(
                "env.realize_failed", "session installer failed", stage="realize",
                hints={"out_tail": _BUILD_FAIL, "rc": 0,
                       "failure_class": "missing_system_lib",
                       "missing_system": {"header": "netcdf.h"}})))))
    res = discovery._ensure_r_via_session(
        {"name": "RNetCDF", "provisioning": {"r": {"source": "cran", "package": "RNetCDF"}}},
        {}, None, "RNetCDF")
    note = res["note"]
    assert res["status"] == "error"
    assert "netcdf.h" in note, "the cause must still be there"
    assert "make_isolated_env" in note, (
        f"no way forward offered; agent sees only the failure: {note[-200:]!r}")
    assert "language='r'" in note, "the remedy must be callable as written"
    # and it must say WHY retrying in the project env is pointless
    assert "system library" in note.lower()
    # Stage E: the LONG rationale (viewer caveat, base-pack routing) lives in
    # the env-failures playbook (system bundle rule), not in every error —
    # the note references the playbook and stays lean.
    assert "playbook" in note.lower(), (
        "the note must point at the playbook that carries the full doctrine")
    assert len(note.split("NEXT STEP", 1)[1]) < 520, (
        f"way-out prose crept back toward paragraph size: "
        f"{len(note.split('NEXT STEP', 1)[1])} chars")


def test_way_out_fires_only_on_the_typed_class():
    """The classifier's end-state (tracked since the campaign began): the
    substrate tags `hints.failure_class: missing_system_lib` on build
    failures; the remedy keys on THAT — no stage gates, no code gates, no
    text matching. Anything else — typo'd names, 404s, exec failures, build
    logs quoted in fused notes — gets no lecture, because it carries no tag."""
    from content.bio.tools.discovery import _syslib_way_out
    assert _syslib_way_out("x", "x",
                           failure_class="missing_system_lib") != ""
    for fc in (None, "", "something_else"):
        assert _syslib_way_out("x", "x", failure_class=fc) == "", fc
    # the captured name (when the substrate's scan found one) is surfaced
    out = _syslib_way_out("x", "x", failure_class="missing_system_lib",
                          missing_system={"header": "png.h"})
    assert "png.h" in out


def test_text_sign_taxonomy_is_deleted():
    """_SYSLIB_SIGNS was always a FALLBACK awaiting a typed discrimination;
    the substrate now ships one. The text taxonomy must not survive — a
    revived copy would re-open the locale/false-positive class the typed
    tag closed."""
    from content.bio.tools import discovery
    assert not hasattr(discovery, "_SYSLIB_SIGNS"), "the text taxonomy is back"
    assert not hasattr(discovery, "_PRE_BUILD_STAGES"), (
        "the stage gate outlived the text signs it guarded")


def test_flow_retryable_index_failure_gets_retry_nudge_not_lecture(monkeypatch):
    """A dead repository index is transient — weft types it env.solve_failed +
    retryable. The note must say retry, and must NOT lecture about system
    libraries even when the fused note quotes a configure error from the
    lane's earlier decline (both contributors are typed resolution-class)."""
    from core.compute.errors import ComputeError

    def _boom(*a, **k):
        raise ComputeError(
            "env.solve_failed",
            "an R repository index is unreachable from this node",
            stage="realize", retryable=True,
            hints={"err_tail": "unable to access index for repository …"})

    discovery = _flow(
        monkeypatch,
        lane=lambda *a, **k: (False, _BUILD_FAIL, {"code": "env.solve_failed"}),
        installer=_boom)
    res = discovery._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "cran", "package": "p"}}},
        {}, None, "p")
    note = res["note"]
    assert res["status"] == "error"
    assert "RETRYABLE" in note, f"the substrate's retryable verdict dropped: {note!r}"
    assert "missing SYSTEM library" not in note, (
        "resolution-class failure got the build-stage lecture")


def test_flow_typed_build_failure_keeps_lecture_no_retry_nudge(monkeypatch):
    """The OTHER side: a typed realize_failed with a build signature keeps the
    remedy, and a non-retryable failure gets no retry advice."""
    from core.compute.errors import ComputeError

    def _boom(*a, **k):
        raise ComputeError(
            "env.realize_failed",
            "installing the R delta into the session layer failed",
            stage="realize", retryable=False,
            hints={"err_tail": _BUILD_FAIL, "install_rc": 1, "verify_rc": 0,
                   "failure_class": "missing_system_lib"})

    discovery = _flow(
        monkeypatch,
        lane=lambda *a, **k: (False, "no cran binary",
                              {"code": "env.realize_failed"}),
        installer=_boom)
    res = discovery._ensure_r_via_session(
        {"name": "RNetCDF",
         "provisioning": {"r": {"source": "cran", "package": "RNetCDF"}}},
        {}, None, "RNetCDF")
    note = res["note"]
    assert "missing SYSTEM library" in note, note
    assert "RETRYABLE" not in note


def test_github_resolved_name_is_adopted_for_verification(monkeypatch):
    """weft returns `resolved` — the DESCRIPTION names it read at install time
    (63b6199). A monorepo/renamed package's load name is NOT the repo tail;
    verifying under the tail reported a SUCCESSFUL install as 'not loadable'
    unless the agent happened to know to pass library=. The substrate already
    knows the name — adopt it."""
    seen: list = []

    def _probe(pid, lib, *a, **k):
        seen.append(lib)
        return "2.1" if lib == "RealName" else None

    discovery = _flow(
        monkeypatch,
        lane=lambda *a, **k: (True, None, {"resolved": ["RealName"]}),
        probe=_probe)
    res = discovery._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "github",
                                             "package": "org/monorepo"}}},
        {}, None, "p")
    assert res["status"] == "ready", (
        f"install landed under 'RealName' but was verified as {seen[-1]!r}: {res}")
    assert res["library"] == "RealName"


def test_github_explicit_library_wins_over_resolved(monkeypatch):
    """WIDE: an explicit library= from the agent stays authoritative over the
    substrate's resolved name, and an ABSENT `resolved` leaves the repo-tail
    heuristic exactly as before."""
    calls = {"n": 0}

    def _probe_for(expect):
        def _probe(pid, lib, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:          # pre-install: nothing loadable yet
                return None
            return "1.0" if lib == expect else None
        return _probe

    discovery = _flow(
        monkeypatch,
        lane=lambda *a, **k: (True, None, {"resolved": ["Other"]}),
        probe=_probe_for("Given"))
    res = discovery._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "github",
                                             "package": "org/repo"}}},
        {"library": "Given"}, None, "p")
    assert res["status"] == "ready" and res["library"] == "Given", res

    calls["n"] = 0
    discovery = _flow(monkeypatch, lane=lambda *a, **k: (True, None, {}),
                      probe=_probe_for("repo"))
    res = discovery._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "github",
                                             "package": "org/repo"}}},
        {}, None, "p")
    assert res["status"] == "ready" and res["library"] == "repo", res


def test_way_out_advice_is_syntactically_valid():
    """The suggested follow-up must be executable as written: on the github
    path `pkg` is owner/repo, and interpolating it into a package spec
    produces an invalid name containing a slash."""
    from content.bio.tools.discovery import _syslib_way_out
    out = _syslib_way_out("MonoPkg", "owner/repo",
                          failure_class="missing_system_lib")
    spec = out.split("packages=['")[1].split("'")[0]
    assert spec == "r-monopkg" and "/" not in spec, out
    # absent library name: fall back to the repo BASENAME, never the path
    out2 = _syslib_way_out("", "owner/repo",
                           failure_class="missing_system_lib")
    spec2 = out2.split("packages=['")[1].split("'")[0]
    assert "/" not in spec2 and spec2 == "r-repo", out2
    # promotion is the durable pattern — it must be the primary suggestion
    assert "set_active_env" in out


def test_fallback_installer_asserts_the_package_landed(monkeypatch):
    """`Rscript -e 'install.packages("x")'` exits 0 even when the build died —
    so the lane reports success and the agent gets "Installed, but library(x)
    is not loadable", with the build log discarded. Found only AFTER the
    missing repos= stopped masking it with an unrelated mirror error."""
    for prov in ({"source": "cran", "package": "RNetCDF"},
                 {"source": "github", "package": "org/repo"},
                 {"source": "bioconductor", "package": "X"}):
        mp = pytest.MonkeyPatch()
        try:
            discovery, seen = _capture_installer(mp)
            try:
                discovery._ensure_r_via_session(
                    {"name": "p", "provisioning": {"r": prov}}, {}, None, "p")
            except Exception:  # noqa: BLE001
                pass
            cmd = seen.get("cmd") or ""
            assert "requireNamespace" in cmd and "quit(status=1)" in cmd, (
                f"{prov['source']} lane cannot fail on a silent no-op: {cmd!r}")
        finally:
            mp.undo()


def _flow(monkeypatch, *, lane, probe=lambda *a, **k: None,
          installer=lambda *a, **k: {"ok": True},
          conda=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cold base"))):
    """_ensure_r_via_session with every boundary stubbed per-call."""
    from content.bio.tools import discovery
    monkeypatch.setattr(discovery, "_r_version_in_session", probe)
    monkeypatch.setattr(discovery, "_cran_lane", lane)
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(install=conda,
                                          run_installer=installer)))
    return discovery


def test_not_loadable_carries_the_diagnosis_and_the_way_out(monkeypatch):
    """The silent-failure exit path returned 89 chars and dropped everything —
    the lane error AND the remedy — even though THIS request's lane decline
    held the build log that named the missing header."""
    discovery = _flow(monkeypatch,
                      lane=lambda *a, **k: (False, _BUILD_FAIL,
                                            {"code": "env.realize_failed",
                                             "failure_class": "missing_system_lib"}))
    res = discovery._ensure_r_via_session(
        {"name": "RNetCDF", "provisioning": {"r": {"source": "cran", "package": "RNetCDF"}}},
        {}, None, "RNetCDF")
    note = res["note"]
    assert res["status"] == "error"
    assert "netcdf.h" in note, f"lane diagnosis dropped: {note!r}"
    assert "make_isolated_env" in note, f"no way forward: {note!r}"


def test_lane_diagnosis_is_request_scoped_not_global(monkeypatch):
    """A decline from an EARLIER request must never surface in a later one:
    the module-global 'last lane error' attributed request A's diagnosis to
    request B (stale — never cleared — and racy across worker threads)."""
    # request A: its lane declines with a distinctive diagnosis
    discovery = _flow(monkeypatch,
                      lane=lambda *a, **k: (False, "A-ONLY-DIAGNOSIS-73", {}))
    discovery._ensure_r_via_session(
        {"name": "pA", "provisioning": {"r": {"source": "cran", "package": "pA"}}},
        {}, None, "pA")
    # request B: its OWN lane lands cleanly; the install then isn't loadable
    discovery = _flow(monkeypatch, lane=lambda *a, **k: (True, None, {}))
    res = discovery._ensure_r_via_session(
        {"name": "pB", "provisioning": {"r": {"source": "cran", "package": "pB"}}},
        {}, None, "pB")
    assert res["status"] == "error"
    assert "A-ONLY-DIAGNOSIS-73" not in res["note"], (
        "request A's lane diagnosis leaked into request B's failure note — "
        "the diagnosis must travel with the request, not through shared state")
    from content.bio.tools import discovery as _d
    assert not hasattr(_d, "_LAST_LANE_ERROR"), "the shared-state slot is back"


def test_min_version_is_rechecked_after_install(monkeypatch):
    """The other side of the landed-check: an upgrade whose build died leaves
    the OLD version loadable — asserting loadability alone reports the
    upgrade as ready."""
    discovery = _flow(monkeypatch, lane=lambda *a, **k: (True, None, {}),
                      probe=lambda *a, **k: "1.0")
    res = discovery._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "cran", "package": "p"}}},
        {"min_version": "2.0"}, None, "p")
    assert res["status"] == "error", (
        f"upgrade build produced only the old 1.0 yet reported: {res}")
    assert "2.0" in res["note"] and "1.0" in res["note"]


def test_library_override_reaches_probe_and_cran_lane(monkeypatch):
    """The load-verify name is agent-overridable: a conda-named package's CRAN
    name is mixed-case, and without the override the retry asks the
    case-sensitive registry for a name that cannot exist there."""
    seen: dict = {}
    discovery = _flow(
        monkeypatch,
        lane=lambda pid, spec, **k: (seen.setdefault("spec", spec),
                                     (True, None, {}))[1],
        probe=lambda pid, lib, *a, **k: seen.setdefault("lib", lib) and None)
    discovery._ensure_r_via_session(
        {"name": "p", "provisioning": {"r": {"source": "conda",
                                             "package": "r-mixedcase"}}},
        {"library": "MixedCase"}, None, "p")
    assert seen.get("spec") == "MixedCase", (
        f"CRAN retry used {seen.get('spec')!r} — the case-correct override "
        f"never reached the lane")
    assert seen.get("lib") == "MixedCase"


# ── 4: the retracted premise ────────────────────────────────────────────────
# The active-env POINTER itself (resolve_env across every lane, set_active's
# language-slot validation) landed upstream in f684f0fd/02de4a8a and is
# census-guarded by tests/test_env_resolution.py — not re-tested here. What
# stays is the prose that made the gap invisible in the first place.


def test_set_active_env_is_agent_facing_for_both_languages():
    """The tool's own prose was the false premise: 'Python only — R's
    per-project library already overrides the base'. True for R PACKAGES,
    false for SYSTEM libraries, which no library dir can carry."""
    import inspect
    from content.bio.mcp_servers.aba_core.tools import discovery as mcp_disc
    src = inspect.getsource(mcp_disc)
    assert "Python only" not in src, "the retracted premise is still advertised"
    assert "already overrides the base" not in src
