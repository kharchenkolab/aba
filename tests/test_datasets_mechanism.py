"""core/data/datasets.py (misc/datasets2.md v2) — the byte-plane mechanism
over a fake port: registration tiers, lazy identity, the drift fence at
ensure_ref (the memo trap), site routing, and the fetch guardrail."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_dsmech_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core.data import datasets as ds  # noqa: E402


class FakePort:
    """Scriptable weft data plane."""

    def __init__(self):
        self.calls: list[tuple] = []
        # path -> list of {path, bytes, mtime} entries
        self.trees: dict[str, list[dict]] = {}
        self.registered: list[dict] = []

    def sync_call(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        if name == "data_fingerprint":
            path, _site = a
            entries = self.trees.get(path)
            if entries is None:
                from core.compute.errors import ComputeError
                raise ComputeError("data.missing", f"no such path {path}")
            return {"entries": entries, "truncated": False}
        if name == "data_register":
            self.registered.append({"a": a, "kw": kw})
            return {"ref": f"dref:{abs(hash((a, tuple(sorted(kw.items()))))) % 10**12}",
                    "bytes": 123, "files": 2,
                    "external_home": a[0] if kw.get("ingest") is False else None}
        if name == "data_fetch":
            return {"ref": a[0], "path": a[1]}
        raise AssertionError(f"unexpected {name}")


@pytest.fixture()
def fake(monkeypatch):
    port = FakePort()
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: port)
    return port


T1 = [{"path": "a.bin", "bytes": 1000, "mtime": 1},
      {"path": "sub/b.bin", "bytes": 500, "mtime": 2}]


# ── source keys + routing ────────────────────────────────────────────────────

def test_source_keys():
    assert ds.source_key("https://x.org/d.tar") == "https://x.org/d.tar"
    assert ds.source_key("/groups/lab/x", "vbc") == "vbc:/groups/lab/x"
    assert ds.source_key("/groups/lab/x") == "local:/groups/lab/x"


def test_site_routing_by_declared_storage(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "h"))
    from core.compute import sites_config
    sites_config.upsert_site("vbc", "slurm",
                             {"root": "/scratch/me/.weft"},
                             aba={"storage": [{"path": "/groups/lab",
                                               "stable": True}]})
    assert ds.resolve_site_for_path("/groups/lab/atlas") == "vbc"
    assert ds.resolve_site_for_path("/scratch/me/other") == "vbc"  # root parent
    assert ds.resolve_site_for_path("/elsewhere/x") == "local"


# ── registration tiers ───────────────────────────────────────────────────────

def test_url_registers_eagerly_as_cas(fake):
    out = ds.register_source("https://x.org/data.h5")
    assert out["origin_class"] == "url" and out["ref"]
    assert out["home"] is None
    name, a, kw = fake.calls[-1]
    assert name == "data_register" and kw.get("site") == "local"


def test_site_path_registers_reference_only(fake):
    fake.trees["/groups/lab/atlas"] = T1
    out = ds.register_source("/groups/lab/atlas", site="vbc")
    assert out["origin_class"] == "path"
    assert out["ref"] is None                      # identity is LAZY
    assert out["home"] == {"site": "vbc", "path": "/groups/lab/atlas"}
    assert out["fingerprint"]["n_files"] == 2
    assert out["descriptor"]["total_bytes"] == 1500
    assert out["descriptor"]["top"] == ["a.bin", "sub"]
    assert not fake.registered                     # nothing ingested


def test_small_data_can_mint_eagerly(fake):
    fake.trees["/groups/lab/tiny"] = T1
    out = ds.register_source("/groups/lab/tiny", site="vbc",
                             eager_ref_max_bytes=10_000)
    assert out["ref"]
    assert fake.registered[-1]["kw"] == {"site": "vbc", "ingest": False}


def test_missing_path_records_home_with_exists_false(fake):
    out = ds.register_source("/groups/lab/gone", site="vbc")
    assert out["fingerprint"] == {"exists": False} and out["ref"] is None


def test_produced_ingests_now(fake):
    out = ds.ingest_produced("/tmp/jobdir/out.parquet")
    assert out["origin_class"] == "run" and out["ref"]
    assert fake.registered[-1]["kw"] == {}         # plain local ingest


# ── first use: lazy identity + the drift fence ───────────────────────────────

def test_ensure_ref_mints_lazily_and_reuses(fake):
    fake.trees["/g/a"] = T1
    meta = ds.register_source("/g/a", site="vbc")
    r1 = ds.ensure_ref(meta)
    assert r1["state"] == "ok" and r1["ref"]
    assert fake.registered[-1]["kw"] == {"site": "vbc", "ingest": False}
    meta["ref"] = r1["ref"]
    n = len(fake.registered)
    r2 = ds.ensure_ref(meta)                        # reuse, but still fenced
    assert r2["ref"] == r1["ref"] and len(fake.registered) == n


def test_ensure_ref_catches_drift_before_memo_can_lie(fake):
    """The live-found trap: identical resubmits memo-hit BEFORE staging, so
    weft's stat-fence never runs — ensure_ref must re-fingerprint."""
    fake.trees["/g/a"] = T1
    meta = ds.register_source("/g/a", site="vbc")
    meta["ref"] = ds.ensure_ref(meta)["ref"]
    fake.trees["/g/a"] = T1[:1] + [{"path": "sub/b.bin", "bytes": 999,
                                    "mtime": 9}]
    out = ds.ensure_ref(meta)
    assert out["state"] == "drifted"
    assert out["fingerprint"]["total_bytes"] == 1999


def test_ensure_ref_reports_missing_home(fake):
    fake.trees["/g/a"] = T1
    meta = ds.register_source("/g/a", site="vbc")
    del fake.trees["/g/a"]
    assert ds.ensure_ref(meta)["state"] == "missing"


def test_cas_backed_meta_skips_fingerprinting(fake):
    meta = ds.register_source("https://x.org/d.h5")
    n = len(fake.calls)
    out = ds.ensure_ref(meta)
    assert out == {"ref": meta["ref"], "state": "ok"}
    assert len(fake.calls) == n                    # no extra round-trips


# ── guardrail + fetch ────────────────────────────────────────────────────────

def test_fetch_guardrail_refuses_big_with_placement_suggestion(fake):
    meta = {"descriptor": {"total_bytes": 3 * 1024**3},
            "home": {"site": "vbc", "path": "/g/a"}}
    out = ds.fetch_check(meta)
    assert not out["ok"] and "vbc" in out["suggestion"]
    r = ds.fetch(meta, "/tmp/x")
    assert r["error"] == "fetch_guardrail"


def test_fetch_small_goes_through(fake):
    fake.trees["/g/a"] = T1
    meta = ds.register_source("/g/a", site="vbc")
    r = ds.fetch(meta, "/tmp/dest")
    assert r["ok"] and r["ref"]
    assert fake.calls[-1][0] == "data_fetch"


def test_fetch_refuses_drifted_source(fake):
    fake.trees["/g/a"] = T1
    meta = ds.register_source("/g/a", site="vbc")
    meta["ref"] = "dref:old"
    fake.trees["/g/a"] = [{"path": "a.bin", "bytes": 1, "mtime": 5}]
    r = ds.fetch(meta, "/tmp/dest")
    assert r["error"] == "source_drifted"
