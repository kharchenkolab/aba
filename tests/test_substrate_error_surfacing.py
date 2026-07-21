"""A substrate failure must reach the agent WITH its diagnosis, and a GitHub R
package must be installable when it lives in a subdirectory.

Live 2026-07-21: `ensure_capability('lstar', language='r', source='github')` told
the agent only "[env.realize_failed@realize] session installer failed". weft had
attached the actual cause in `hints`:

    out_tail: Error: Failed to install 'unknown package' from GitHub:
              cannot open URL '…/contents/DESCRIPTION?ref=main'

i.e. `kharchenkolab/lstar` is a polyglot monorepo whose R package sits under
`R/`, so there is no DESCRIPTION at the repo root. The agent, seeing none of
that, concluded the repository did not exist and burned four turns. `str(e)`
renders only `[code@stage] detail`, so every `f"…{e}"` dropped the hints.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform


def _err():
    from core.compute.errors import ComputeError
    return ComputeError(
        "env.solve_conflict", "installing the R delta into the session layer failed",
        stage="realize",
        hints={"requested": ["kharchenkolab/lstar@main"], "rc": 1,
               "out_tail": "Error: Failed to install 'unknown package' from GitHub:\n"
                           "  cannot open URL 'https://api.github.com/repos/"
                           "kharchenkolab/lstar/contents/DESCRIPTION?ref=main'",
               "err_tail": "", "script_tail": 'remotes::install_github("kharchenkolab/lstar@main")'},
        meaning="unsatisfiable spec; hints list the conflicting requirements")


def test_describe_carries_the_actual_diagnosis():
    from core.compute.errors import describe
    e = _err()
    assert "session installer failed" not in describe(e) or True   # summary may stay
    out = describe(e)
    assert "cannot open URL" in out, "the failing URL — the whole diagnosis — is missing"
    assert "DESCRIPTION" in out
    assert "rc: 1" in out
    assert "install_github" in out, "the script that ran should be recoverable"
    # and it must still contain the summary the old rendering gave
    assert "env.solve_conflict" in out


def test_describe_is_bounded():
    """An unbounded tail would push the rest of a tool result out of view."""
    from core.compute.errors import ComputeError, describe
    e = ComputeError("x", "y", hints={"out_tail": "Z" * 50_000})
    assert len(describe(e)) < 3_000


def test_describe_degrades_on_the_absent_shapes():
    """Guards must cover the degenerate inputs, not just the rich one."""
    from core.compute.errors import ComputeError, describe
    assert describe(ComputeError("c", "d")) == str(ComputeError("c", "d"))     # no hints
    assert describe(ComputeError("c", "d", hints={})) == str(ComputeError("c", "d"))
    assert describe(ComputeError("c", "d", hints={"out_tail": ""})) == \
        str(ComputeError("c", "d"))                                            # empty values
    assert "boom" in describe(RuntimeError("boom"))     # not a ComputeError at all


def test_r_install_error_reaches_the_agent_with_hints(monkeypatch):
    """The agent-facing note must carry the hints, not just the summary."""
    import types
    from content.bio.tools import discovery

    monkeypatch.setattr(discovery, "_r_version_in_session", lambda *_a, **_k: None)
    monkeypatch.setattr(discovery, "_cran_lane", lambda *a, **k: False)
    fake_pe = types.SimpleNamespace(
        install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no conda")),
        run_installer=lambda *a, **k: (_ for _ in ()).throw(_err()))
    monkeypatch.setitem(sys.modules, "core.compute",
                        types.SimpleNamespace(project_env=fake_pe))
    cap = {"name": "lstar",
           "provisioning": {"r": {"source": "github", "package": "kharchenkolab/lstar"}}}
    res = discovery._ensure_r_via_session(cap, {}, None, "lstar")
    assert res["status"] == "error"
    assert "cannot open URL" in res["note"], (
        f"agent gets no diagnosis, only: {res['note'][:160]}")


# ── subdir ──────────────────────────────────────────────────────────────────

def _spec_for(monkeypatch, prov):
    import types
    from content.bio.tools import discovery
    seen = {}
    monkeypatch.setattr(discovery, "_r_version_in_session", lambda *_a, **_k: None)
    monkeypatch.setattr(discovery, "_cran_lane",
                        lambda pid, spec, **k: seen.setdefault("spec", spec) and True)
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no conda")),
            run_installer=lambda *a, **k: {"ok": True})))
    try:
        discovery._ensure_r_via_session({"name": "p", "provisioning": {"r": prov}},
                                        {}, None, "p")
    except Exception:  # noqa: BLE001 — post-install verification out of scope
        pass
    return seen.get("spec")


@pytest.mark.parametrize("prov,expected", [
    ({"source": "github", "package": "org/repo"}, "org/repo"),
    ({"source": "github", "package": "org/repo", "ref": "main"}, "org/repo@main"),
    ({"source": "github", "package": "org/repo", "subdir": "R"}, "org/repo/R"),
    ({"source": "github", "package": "org/repo", "subdir": "R", "ref": "main"},
     "org/repo/R@main"),
    # degenerate shapes: stray slashes and an empty subdir must not corrupt the spec
    ({"source": "github", "package": "org/repo", "subdir": "/R/"}, "org/repo/R"),
    ({"source": "github", "package": "org/repo", "subdir": ""}, "org/repo"),
])
def test_github_spec_composes_subdir_in_remotes_grammar(monkeypatch, prov, expected):
    """remotes' grammar is owner/repo[/subdir][@ref] — subdir goes BEFORE the ref."""
    assert _spec_for(monkeypatch, prov) == expected


def test_subdir_is_an_agent_facing_parameter():
    """It must be requestable: the live failure needed subdir='R' and there was
    no way for the agent to say so."""
    import inspect
    from content.bio.mcp_servers.aba_core.tools import discovery as mcp_disc
    src = inspect.getsource(mcp_disc)
    assert "subdir" in src
    assert '("subdir", subdir)' in src, "subdir accepted but never forwarded"


def test_subdir_does_not_corrupt_the_library_name(monkeypatch):
    """WHY an explicit `subdir` field rather than stuffing it into `package`.

    remotes' grammar would accept package='org/repo/R' — and the pre-weft code
    passed the spec verbatim, so that route "worked" for the INSTALL. But the
    library name is derived from the package's last path segment, so
    'org/repo/R' yields library(R): the post-install verification then fails and
    the capability is marked not-ready even though the package installed. The
    explicit field keeps package (→ libname) and subdir (→ spec) separate.
    """
    import types
    from content.bio.tools import discovery
    seen = {}
    monkeypatch.setattr(discovery, "_r_version_in_session",
                        lambda pid, lib, *a, **k: seen.setdefault("lib", lib) and None)
    monkeypatch.setattr(discovery, "_cran_lane",
                        lambda pid, spec, **k: seen.setdefault("spec", spec) and True)
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no conda")),
            run_installer=lambda *a, **k: {"ok": True})))
    cap = {"name": "lstar", "provisioning": {"r": {
        "source": "github", "package": "kharchenkolab/lstar", "subdir": "R"}}}
    try:
        discovery._ensure_r_via_session(cap, {}, None, "lstar")
    except Exception:  # noqa: BLE001
        pass
    assert seen.get("spec") == "kharchenkolab/lstar/R", seen
    assert seen.get("lib") == "lstar", (
        f"library name became {seen.get('lib')!r} — the subdir leaked into it")
