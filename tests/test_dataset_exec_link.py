"""A registered dataset links the producing run's exec record so its provenance shows
the fetch code + env + source (misc/provenance.md). Explicit exec_id wins; else the most
recent run in the thread is auto-linked. Regression 2026-07-12 (downloaded dataset showed
empty provenance: imported(GEO) with exec_id=None).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.graph.exec_records as er              # noqa: E402
from content.bio.tools import curation            # noqa: E402


def test_explicit_exec_id_wins(monkeypatch):
    monkeypatch.setattr(er, "latest_exec_id_for_thread", lambda t: "ex_LATEST")
    assert curation._producing_exec_id({"exec_id": "ex_EXPLICIT"}, {"thread_id": "t1"}) == "ex_EXPLICIT"
    # whitespace-only explicit → treated as absent → falls back
    assert curation._producing_exec_id({"exec_id": "  "}, {"thread_id": "t1"}) == "ex_LATEST"


def test_falls_back_to_latest_in_thread(monkeypatch):
    monkeypatch.setattr(er, "latest_exec_id_for_thread", lambda t: "ex_LATEST")
    assert curation._producing_exec_id({}, {"thread_id": "t1"}) == "ex_LATEST"


def test_none_when_no_exec_available(monkeypatch):
    monkeypatch.setattr(er, "latest_exec_id_for_thread", lambda t: None)
    assert curation._producing_exec_id({}, {"thread_id": "t1"}) is None


def test_latest_exec_id_for_thread_empty_thread():
    assert er.latest_exec_id_for_thread("") is None
    assert er.latest_exec_id_for_thread(None) is None
