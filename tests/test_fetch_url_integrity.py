"""Guard: fetch_url prefers HTTPS over FTP and refuses a truncated transfer.

Regression (live 2026-07-21): an ftp:// GEO download truncated silently on the
compute node — urllib's ftp handler reports no Content-Length, so a short file was
written and returned status:ok, only to fail later as 'corrupt gzip'. Fix: rewrite
known FTP hosts to HTTPS (verifiable Content-Length) and retry a short read instead
of handing back a partial file."""
from __future__ import annotations
import urllib.request

import pytest

from content.bio.tools.discovery import _prefer_https, fetch_url

pytestmark = pytest.mark.platform


def test_prefer_https_rewrites_known_ftp_hosts():
    assert _prefer_https("ftp://ftp.ncbi.nlm.nih.gov/geo/x/matrix.mtx.gz") == \
        "https://ftp.ncbi.nlm.nih.gov/geo/x/matrix.mtx.gz"
    assert _prefer_https("ftp://ftp.ensembl.org/pub/release-110/x.gtf.gz").startswith("https://")
    # unknown ftp host + already-https are left untouched
    assert _prefer_https("ftp://example.org/f").startswith("ftp://")
    assert _prefer_https("https://ftp.ncbi.nlm.nih.gov/x").startswith("https://")


class _FakeResp:
    def __init__(self, data: bytes, clen):
        self._d, self._i = data, 0
        self.headers = {} if clen is None else {"Content-Length": str(clen)}

    def read(self, n):
        c = self._d[self._i:self._i + n]; self._i += len(c); return c

    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def _wire(monkeypatch, tmp_path):
    from core.data import workspace
    from core.graph import audit
    from core import projects
    monkeypatch.setattr(workspace, "scratch_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(audit, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(projects, "current", lambda: "test")


def test_truncated_transfer_is_rejected_after_retries(_wire, monkeypatch, tmp_path):
    calls = {"n": 0}
    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        return _FakeResp(b"x" * 50, clen=100)     # server promised 100, sends 50
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = fetch_url({"url": "https://ftp.ncbi.nlm.nih.gov/geo/matrix.mtx.gz"})
    assert "error" in r and "truncat" in r["error"].lower(), r
    assert calls["n"] == 3, "should retry a truncated transfer, not accept it"
    # the short file must NOT be left on disk to later read as corrupt
    leftover = list(tmp_path.glob("matrix.mtx.gz"))
    assert leftover == [], f"partial file leaked after exhausted retries: {leftover}"


def test_complete_transfer_verifies_ok(_wire, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResp(b"x" * 100, clen=100))
    r = fetch_url({"url": "https://ftp.ncbi.nlm.nih.gov/geo/matrix.mtx.gz"})
    assert r.get("status") == "ok" and r.get("bytes") == 100 and r.get("verified") is True, r
