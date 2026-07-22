"""ensure_capability language scoping: readiness is per-RUNTIME.

The bug class: the catalog is keyed by name alone, so a request from an R
session could get a confident `status: ready` that was true only for Python —
a success in a scope the caller never asked about. These guards pin the fix:
  - explicit `language=` conflicting with the catalogued ecosystem re-routes
    (exact registry hit → that ecosystem's install) instead of answering for
    the wrong runtime;
  - the Python import probe never answers an R-scoped request;
  - success responses carry `ready_in` as a branchable field;
  - `language=` conflicting with `env=`'s recorded language refuses;
  - inference uses the single live kernel and DECLINES when ambiguous.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import content.bio.tools.discovery as d  # noqa: E402

pytestmark = pytest.mark.bio


def _py_cap(name="foo"):
    return {"name": name, "archetype": "library",
            "provisioning": {"pip": [name]}}


def test_r_request_on_python_catalogued_name_reroutes_to_cran(monkeypatch):
    """The note's exact scenario: R session, Python-catalogued 'foo'. Must NOT
    answer ready-for-python; with an exact CRAN hit it installs the R package.
    The deployment R-pack gate is stubbed PROVISIONED here (hermetic CI has no
    R pack; the unprovisioned refusal is its own test below), and the r-bio
    MODULE toggle is stubbed ON — the deployment's setting must not decide a
    hermetic guard (found flaky when the live deployment's toggle flipped)."""
    monkeypatch.setattr(d, "_r_module_block", lambda: None)
    monkeypatch.setattr("core.compute.base_env.require", lambda lang: None)
    monkeypatch.setattr("core.catalog.resolve_capability", lambda n: _py_cap(n))
    monkeypatch.setattr(d, "_cran_exact",
                        lambda n, **k: {"source": "cran", "package": n})
    seen: dict = {}

    def _fake_r(cap, input_, ctx, name, **k):
        seen["cap"] = cap
        return {"status": "ready", "name": name, "archetype": "r_package",
                "library": name, "note": "installed"}
    monkeypatch.setattr(d, "_ensure_r_via_session", _fake_r)
    r = d.ensure_capability({"name": "foo", "language": "r"}, {"thread_id": "t"})
    assert r["status"] == "ready", r
    assert r.get("ready_in") == "r", r
    assert seen["cap"]["archetype"] == "r_package"
    assert "catalogued for python" in (r.get("note") or "").lower()


def test_r_request_never_satisfied_by_python_import_probe(monkeypatch):
    """Uncatalogued name, language='r': the run_python import probe is the
    wrong oracle and must not run; with no CRAN hit the answer is candidates/
    not_found — never a Python 'ready'."""
    monkeypatch.setattr("core.catalog.resolve_capability", lambda n: None)
    monkeypatch.setattr(d, "_cran_exact", lambda n, **k: None)

    def _boom(*a, **k):
        raise AssertionError("python import probe ran for an R-scoped request")
    monkeypatch.setattr("core.exec.verify.verify_python_imports", _boom)
    monkeypatch.setattr(d, "_search_external_for_name", lambda n, **k: [])
    r = d.ensure_capability({"name": "somepkg", "language": "r"}, {"thread_id": "t"})
    assert r["status"] in ("candidates", "not_found"), r


def test_language_env_conflict_refuses(monkeypatch):
    monkeypatch.setattr("core.compute.named_envs.resolve",
                        lambda pid, env: {"language": "python"})
    r = d.ensure_capability({"name": "foo", "env": "warm", "language": "r"},
                            {"thread_id": "t"})
    assert r["status"] == "error"
    assert "conflict" in (r.get("note") or "").lower()


def test_ready_in_stamped_on_python_path(monkeypatch):
    """The historical path (python probe hit) now names its scope."""
    monkeypatch.setattr("core.catalog.resolve_capability", lambda n: None)
    monkeypatch.setattr(d, "_default_probe_argv", lambda: ["python"])
    monkeypatch.setattr("core.exec.verify.verify_python_imports",
                        lambda probes, argv_builder=None: (True, ""))
    r = d.ensure_capability({"name": "json5", "language": "python"},
                            {"thread_id": "t"})
    assert r["status"] == "ready" and r.get("ready_in") == "python", r


def test_inference_declines_when_ambiguous(monkeypatch):
    """Both kernels live → no guess. (Inference may decline, never guess.)"""
    class _P:
        def peek(self, tid, lang):
            return object()          # both languages 'live'
    monkeypatch.setattr("core.exec.kernels.get_pool", lambda: _P())
    assert d._infer_language({"thread_id": "t"}) is None


def test_inference_uses_single_live_kernel(monkeypatch):
    class _P:
        def peek(self, tid, lang):
            return object() if lang == "r" else None
    monkeypatch.setattr("core.exec.kernels.get_pool", lambda: _P())
    assert d._infer_language({"thread_id": "t"}) == "r"


def test_r_reroute_on_unprovisioned_deployment_refuses_honestly(monkeypatch):
    """A deployment with no R pack must refuse the re-routed R install with the
    provisioning fact named — never a Python 'ready', never a crash. (This is
    the gate CI's hermetic box exercises for real; stubbed here for both
    directions per the armed-guard convention.)"""
    from core.compute.errors import ComputeError

    def _no_pack(lang):
        raise ComputeError("no_base_pack", f"no base environment pack for {lang!r}")
    monkeypatch.setattr(d, "_r_module_block", lambda: None)
    monkeypatch.setattr("core.compute.base_env.require", _no_pack)
    monkeypatch.setattr("core.catalog.resolve_capability", lambda n: _py_cap(n))
    monkeypatch.setattr(d, "_cran_exact",
                        lambda n, **k: {"source": "cran", "package": n})
    r = d.ensure_capability({"name": "foo", "language": "r"}, {"thread_id": "t"})
    assert r["status"] == "error", r
    assert "not available" in (r.get("note") or "") or "no_base_pack" in str(r), r
    assert r.get("ready_in") is None
