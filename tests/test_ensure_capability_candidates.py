"""E-1 — ensure_capability auto-search on miss.

Three layers:
- Per-source helpers (_pypi_exact / _cran_exact / _bioc_exact / _bioconda_exact)
  return propose_capability-shaped dicts on strict name matches, None
  otherwise. Each helper unit-tested with mocked HTTP.
- The orchestrator _search_external_for_name runs the helpers in parallel,
  filters Nones, returns ordered candidates.
- ensure_capability on a catalog miss returns {status:'candidates'} when
  the external search found anything, falls back to {status:'not_found'}
  with the improved no-match note when nothing turned up.

Run: .venv/bin/python tests/test_ensure_capability_candidates.py
"""
from __future__ import annotations
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_ensure_cands_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

# Imports after the env shim — modules wire to ABA_RUNTIME_DIR at import.
from content.bio.tools.discovery import (    # noqa: E402
    _pypi_exact, _cran_exact, _bioc_exact, _bioconda_exact,
    _search_external_for_name, ensure_capability,
)


# ─── helpers ───────────────────────────────────────────────────────────────
class _FakeResp:
    """Minimal context-manager facade over urllib.request.urlopen()."""
    def __init__(self, body: bytes = b"", status: int = 200):
        self._body = body
        self.status = status
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _HTTPError(Exception):
    """Stand-in for urllib.error.HTTPError (with `.code` attribute)."""
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


def _ok_pypi_body(name: str, version: str = "2.0.4",
                  summary: str = "GEO parser") -> bytes:
    return json.dumps({"info": {
        "name": name, "version": version, "summary": summary,
        "home_page": f"https://pypi.org/project/{name}/",
    }}).encode("utf-8")


def _ok_cran_body(pkg: str, version: str = "5.0.1",
                  title: str = "Single-cell analysis") -> bytes:
    return json.dumps({"Package": pkg, "Version": version, "Title": title}).encode("utf-8")


# ─── _pypi_exact ───────────────────────────────────────────────────────────
def test_pypi_exact_returns_candidate_on_strict_match():
    with patch("content.bio.tools.simple.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(_ok_pypi_body("GEOparse"))
        c = _pypi_exact("GEOparse")
    assert c is not None
    assert c["source"] == "pypi"
    assert c["archetype"] == "library"
    assert c["package"] == "GEOparse"
    assert c["version"] == "2.0.4"


def test_pypi_exact_accepts_pep503_variant():
    """PyPI normalizes scikit_learn <-> scikit-learn; both should match
    strictly under PEP-503 canonicalization."""
    with patch("content.bio.tools.simple.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(_ok_pypi_body("scikit-learn"))
        c = _pypi_exact("scikit_learn")
    assert c is not None
    assert c["package"] == "scikit-learn"


def test_pypi_exact_rejects_canonical_name_mismatch():
    """If PyPI's canonical name is wholly different from the input (typo
    or shadow project), strict rule rejects."""
    with patch("content.bio.tools.simple.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(_ok_pypi_body("entirely-different-package"))
        c = _pypi_exact("foobar")
    assert c is None


def test_pypi_exact_returns_none_on_404():
    """Caller already returns {found: False} on 404 — _pypi_exact filters
    that out."""
    import urllib.error
    with patch("content.bio.tools.simple.urllib.request.urlopen") as op:
        op.side_effect = urllib.error.HTTPError(
            "u", 404, "not found", {}, io.BytesIO(b""))
        c = _pypi_exact("never-existed")
    assert c is None


# ─── _cran_exact ───────────────────────────────────────────────────────────
def test_cran_exact_returns_candidate_on_strict_match():
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(_ok_cran_body("Seurat"))
        c = _cran_exact("Seurat")
    assert c is not None
    assert c["source"] == "cran"
    assert c["archetype"] == "r_package"
    assert c["package"] == "Seurat"
    assert c["library"] == "Seurat"
    assert c["version"] == "5.0.1"


def test_cran_exact_case_insensitive_strict():
    """crandb may return canonical capitalization (Seurat); we match
    case-insensitively against what the agent asked for."""
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(_ok_cran_body("Seurat"))
        c = _cran_exact("seurat")
    assert c is not None
    assert c["package"] == "Seurat"


def test_cran_exact_rejects_canonical_name_mismatch():
    """crandb returns a redirect to a different package — strict rejects."""
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(_ok_cran_body("DifferentPackage"))
        c = _cran_exact("Seurat")
    assert c is None


def test_cran_exact_returns_none_on_404():
    import urllib.error
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.side_effect = urllib.error.HTTPError(
            "u", 404, "not found", {}, io.BytesIO(b""))
        c = _cran_exact("nonexistent-pkg")
    assert c is None


def test_cran_exact_returns_none_on_network_error():
    """Network failure (DNS/timeout) shouldn't blow up — return None."""
    import urllib.error
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.side_effect = urllib.error.URLError("dns")
        c = _cran_exact("Seurat")
    assert c is None


# ─── _bioc_exact ───────────────────────────────────────────────────────────
def test_bioc_exact_returns_candidate_on_200():
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(b"", status=200)
        c = _bioc_exact("DESeq2")
    assert c is not None
    assert c["source"] == "bioconductor"
    assert c["package"] == "DESeq2"


def test_bioc_exact_returns_none_on_404():
    import urllib.error
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.side_effect = urllib.error.HTTPError(
            "u", 404, "not found", {}, io.BytesIO(b""))
        c = _bioc_exact("nonexistent")
    assert c is None


# ─── _bioconda_exact ───────────────────────────────────────────────────────
def test_bioconda_exact_returns_candidate_on_match():
    fake = json.dumps({"latest_version": "1.21", "summary": "samtools",
                       "name": "samtools"}).encode("utf-8")
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.return_value = _FakeResp(fake)
        c = _bioconda_exact("samtools")
    assert c is not None
    assert c["source"] == "bioconda"
    assert c["archetype"] == "cli"
    assert c["package"] == "samtools"
    assert c["version"] == "1.21"


def test_bioconda_exact_returns_none_on_404():
    import urllib.error
    with patch("content.bio.tools.discovery.urllib.request.urlopen") as op:
        op.side_effect = urllib.error.HTTPError(
            "u", 404, "not found", {}, io.BytesIO(b""))
        c = _bioconda_exact("nonexistent")
    assert c is None


# ─── _search_external_for_name ─────────────────────────────────────────────
def test_search_external_returns_each_source_strict_match():
    """When all sources return a hit, orchestrator returns all four
    candidates in the documented order."""
    def _stub(src, ret):
        return lambda *a, **k: ret
    with patch("content.bio.tools.discovery._pypi_exact",
               _stub("pypi", {"source": "pypi", "archetype": "library",
                              "package": "X"})), \
         patch("content.bio.tools.discovery._cran_exact",
               _stub("cran", {"source": "cran", "archetype": "r_package",
                              "package": "X", "library": "X"})), \
         patch("content.bio.tools.discovery._bioc_exact",
               _stub("bioc", {"source": "bioconductor", "archetype": "r_package",
                              "package": "X", "library": "X"})), \
         patch("content.bio.tools.discovery._bioconda_exact",
               _stub("bioconda", {"source": "bioconda", "archetype": "cli",
                                  "package": "X"})):
        cands = _search_external_for_name("X")
    sources = [c["source"] for c in cands]
    assert sources == ["cran", "bioconductor", "pypi", "bioconda"], sources


def test_search_external_filters_none_sources():
    """Sources that return None must be omitted (not appear as null slots)."""
    with patch("content.bio.tools.discovery._pypi_exact", return_value=None), \
         patch("content.bio.tools.discovery._cran_exact",
               return_value={"source": "cran", "archetype": "r_package",
                             "package": "Seurat", "library": "Seurat"}), \
         patch("content.bio.tools.discovery._bioc_exact", return_value=None), \
         patch("content.bio.tools.discovery._bioconda_exact", return_value=None):
        cands = _search_external_for_name("Seurat")
    assert len(cands) == 1
    assert cands[0]["source"] == "cran"


def test_search_external_empty_when_all_miss():
    with patch("content.bio.tools.discovery._pypi_exact", return_value=None), \
         patch("content.bio.tools.discovery._cran_exact", return_value=None), \
         patch("content.bio.tools.discovery._bioc_exact", return_value=None), \
         patch("content.bio.tools.discovery._bioconda_exact", return_value=None):
        assert _search_external_for_name("nonexistent-xyz") == []


def test_search_external_swallows_per_source_exceptions():
    """One source raising must not nuke the others' candidates."""
    def _boom(*a, **k):
        raise RuntimeError("network is on fire")
    with patch("content.bio.tools.discovery._pypi_exact", side_effect=_boom), \
         patch("content.bio.tools.discovery._cran_exact",
               return_value={"source": "cran", "archetype": "r_package",
                             "package": "ok", "library": "ok"}), \
         patch("content.bio.tools.discovery._bioc_exact", return_value=None), \
         patch("content.bio.tools.discovery._bioconda_exact", return_value=None):
        cands = _search_external_for_name("ok")
    assert len(cands) == 1
    assert cands[0]["source"] == "cran"


def test_search_external_language_filter_python_only():
    """E-3 plumbing: language='python' restricts to PyPI."""
    pypi_seen = {"hit": False}
    cran_seen = {"hit": False}
    def _pypi(*a, **k):
        pypi_seen["hit"] = True
        return None
    def _cran(*a, **k):
        cran_seen["hit"] = True
        return None
    with patch("content.bio.tools.discovery._pypi_exact", _pypi), \
         patch("content.bio.tools.discovery._cran_exact", _cran), \
         patch("content.bio.tools.discovery._bioc_exact", return_value=None), \
         patch("content.bio.tools.discovery._bioconda_exact", return_value=None):
        _search_external_for_name("X", language="python")
    assert pypi_seen["hit"] is True
    assert cran_seen["hit"] is False, "language='python' must skip CRAN"


# ─── ensure_capability integration ─────────────────────────────────────────
def test_ensure_capability_miss_returns_candidates_when_search_hits():
    """The headline behavior — agent gets propose-shaped suggestions
    in one call instead of being told to go list_capabilities."""
    fake_cands = [
        {"source": "cran", "archetype": "r_package",
         "package": "Seurat", "library": "Seurat", "version": "5.0.1"},
    ]
    with patch("core.catalog.resolve_capability",
               return_value=None), \
         patch("content.bio.tools.discovery._search_external_for_name",
               return_value=fake_cands):
        out = ensure_capability({"name": "Seurat"})
    assert out["status"] == "candidates"
    assert out["name"] == "Seurat"
    assert out["suggestions"] == fake_cands
    # Note must point at propose_capability (not list_capabilities).
    assert "propose_capability" in out["note"]
    assert "list_capabilities" not in out["note"]


def test_ensure_capability_miss_returns_not_found_when_search_empty():
    """When no external registry matches either, still emit not_found —
    but with a richer note (mentions the sources searched)."""
    with patch("core.catalog.resolve_capability",
               return_value=None), \
         patch("content.bio.tools.discovery._search_external_for_name",
               return_value=[]):
        out = ensure_capability({"name": "definitely-not-real-xyz"})
    assert out["status"] == "not_found"
    assert out["name"] == "definitely-not-real-xyz"
    # The new no-match note mentions the registries we checked
    note = out["note"]
    for src in ("PyPI", "CRAN", "Bioconductor", "bioconda"):
        assert src in note, f"new not_found note must name {src}; got: {note}"


def test_ensure_capability_catalog_hit_unchanged():
    """If the name IS in the catalog, the search path must not fire —
    behavior identical to pre-E-1. We use a 'proposed' status (not yet
    approved) so the function returns immediately without crossing into
    the install path — keeps this test orthogonal to install plumbing."""
    fake_cap = {"name": "scanpy", "version": "1.10",
                "provisioning": {"pip": "scanpy"}, "status": "proposed"}
    called = {"searched": False}
    def _search_spy(*a, **k):
        called["searched"] = True
        return []
    with patch("core.catalog.resolve_capability",
               return_value=fake_cap), \
         patch("content.bio.tools.discovery._search_external_for_name",
               side_effect=_search_spy):
        out = ensure_capability({"name": "scanpy"})
    # The catalog hit must short-circuit the external search entirely.
    assert called["searched"] is False, "catalog hit must short-circuit the search"
    # And the result must NOT be the new 'candidates' path.
    assert out.get("status") != "candidates"
    # Sanity: this 'proposed' cap hits the awaiting_approval branch.
    assert out.get("status") == "awaiting_approval"


# ─── runner ────────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
