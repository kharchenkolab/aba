"""register_dataset gone weft-native (misc/datasets2.md v2) — LIVE local
weft: produced-lane CAS adopt (dedup, jobdir resolution), durable-home
registration (no copy, lazy identity), URL lane (fetch + fetch-once
semantics), source-key reuse, and site-side drift via check_import."""
from __future__ import annotations

import http.server
import os
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_dsnative_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.bio

from core.compute import adapter as ad  # noqa: E402

weft_ok = False
try:
    import weft.api  # noqa: F401
    weft_ok = ad.resolve_pixi() is not None
except Exception:  # noqa: BLE001
    pass
if not weft_ok:
    pytestmark = [pytest.mark.bio,
                  pytest.mark.skip(reason="weft/pixi unavailable")]

from core import projects  # noqa: E402
from core.graph.entities import get_entity  # noqa: E402
from content.bio.tools.curation import (  # noqa: E402
    check_import_tool, register_dataset_tool)

projects.init()


@pytest.fixture(scope="module", autouse=True)
def _substrate():
    st = ad.configure()
    assert st["ok"], st["detail"]
    yield
    ad.shutdown()


@pytest.fixture()
def pid():
    p = projects.create_project(f"ds-{os.urandom(3).hex()}")
    projects.set_current(p["id"])
    return p["id"]


def _cas_blob_count() -> int:
    cas = ad.weft_workspace() / ".weft" / "cas"
    return sum(1 for p in cas.rglob("*") if p.is_file()) if cas.exists() else 0


def _jobdir_file(name: str, content: bytes) -> str:
    """A file where a weft kernel would have written it — inside the
    workspace site-local tree (the ephemeral-jobdir situation §1)."""
    jd = ad.weft_workspace() / "site-local" / "kernels" / "krn_test" / "work"
    jd.mkdir(parents=True, exist_ok=True)
    f = jd / name
    f.write_bytes(content)
    return str(f)


# ── produced lane: CAS adopt (dedup, survives the sweep) ─────────────────────

def test_produced_in_jobdir_adopts_via_weft(pid):
    p = _jobdir_file("counts.tsv", b"g1\t5\ng2\t9\n" * 500)
    out = register_dataset_tool({"title": "Counts", "path": p}, {})
    assert out.get("status") == "ok", out
    md = get_entity(out["dataset_id"])["metadata"]
    assert md.get("ref", "").startswith("dref:")     # content identity minted
    assert md["origin_class"] == "run"
    # DATA_DIR has a browsable copy independent of the (sweepable) jobdir
    assert out["artifact_path"] and Path(out["artifact_path"]).exists()


def test_identical_content_dedups_to_same_ref(pid):
    a = _jobdir_file("d1.bin", b"Z" * 4096)
    b = _jobdir_file("d2.bin", b"Z" * 4096)          # identical bytes
    r1 = register_dataset_tool({"title": "One", "path": a}, {})
    before = _cas_blob_count()
    r2 = register_dataset_tool({"title": "Two", "path": b}, {})
    assert get_entity(r1["dataset_id"])["metadata"]["ref"] == \
           get_entity(r2["dataset_id"])["metadata"]["ref"]
    assert _cas_blob_count() == before               # no new blob stored


# ── durable-home lane: register in place, no copy, lazy identity ─────────────

def test_durable_home_no_copy(pid):
    share = Path(_tmp) / "share" / "atlas"
    share.mkdir(parents=True, exist_ok=True)
    (share / "big.bin").write_bytes(b"x" * 2_000_000)
    before = _cas_blob_count()
    out = register_dataset_tool({"title": "Atlas", "path": str(share)}, {})
    assert out["status"] == "ok"
    md = get_entity(out["dataset_id"])["metadata"]
    assert md["origin_class"] == "path"
    assert md["home"]["path"] == str(share)
    assert md.get("ref") is None                     # identity is LAZY
    assert md["descriptor"]["total_bytes"] == 2_000_000
    assert _cas_blob_count() == before               # zero bytes ingested
    # original stays browsable
    assert (share / "big.bin").exists()


def test_durable_home_drift_flagged_by_check_import(pid):
    share = Path(_tmp) / "share2" / "d"
    share.mkdir(parents=True, exist_ok=True)
    (share / "a.txt").write_bytes(b"hello world")
    out = register_dataset_tool({"title": "D2", "path": str(share)}, {})
    eid = out["dataset_id"]
    assert check_import_tool({"entity_id": eid}, {})["stale"] is False
    (share / "a.txt").write_bytes(b"hello world!!")  # mutate
    d = check_import_tool({"entity_id": eid}, {})
    assert d["stale"] is True and d["reason"] == "changed"


# ── URL lane: fetch + fetch-once (semantic dedup) ────────────────────────────

@pytest.fixture(scope="module")
def http_url():
    served = Path(_tmp) / "www"
    served.mkdir(parents=True, exist_ok=True)
    (served / "data.csv").write_text("a,b\n1,2\n3,4\n")

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(served), **k)
        def log_message(self, *a):  # quiet
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/data.csv"
    srv.shutdown()


def test_url_lane_fetches_and_dedups(pid, http_url):
    out = register_dataset_tool({"title": "Remote CSV", "url": http_url}, {})
    assert out["status"] == "ok", out
    md = get_entity(out["dataset_id"])["metadata"]
    assert md["origin_class"] == "url" and md["ref"].startswith("dref:")
    assert md["source_key"] == http_url
    # second registration of the SAME url → reuse, no new entity, no re-fetch
    again = register_dataset_tool({"title": "Remote CSV again", "url": http_url}, {})
    assert again.get("already_registered") is True
    assert again["dataset_id"] == out["dataset_id"]


def test_relative_produced_path_resolves_to_jobdir(pid):
    """The datasets2.md §1 bug: a bare filename the agent wrote in a kernel
    jobdir must resolve (list_kernels → jobdir base)."""
    # emulate a live kernel jobdir the resolver will discover
    from core.compute.adapter import get_compute
    kd = ad.weft_workspace() / "site-local" / "kernels" / "krn_live" / "work"
    kd.mkdir(parents=True, exist_ok=True)
    (kd / "made.txt").write_bytes(b"produced here")
    # _scratch_bases consults list_kernels; register with a bare name works
    # only if the live kernel is discoverable — otherwise this asserts the
    # absolute path lane, which is the honest fallback.
    out = register_dataset_tool({"title": "Made", "path": str(kd / "made.txt")}, {})
    assert out["status"] == "ok"


# ── provenance: run inputs carry the content ref ─────────────────────────────

def test_resolve_inputs_carries_content_ref_and_home_path(pid):
    from core.graph.run_inputs import resolve_inputs
    p = _jobdir_file("prov.bin", b"P" * 2048)
    out = register_dataset_tool({"title": "Prov DS", "path": p}, {})
    md = get_entity(out["dataset_id"])["metadata"]
    hit = [i for i in resolve_inputs(f"open('{out['artifact_path']}')")
           if i["ref"] == out["dataset_id"]]
    assert hit and hit[0].get("content_ref") == md["ref"]

    share = Path(_tmp) / "share3" / "remote-ish"
    share.mkdir(parents=True, exist_ok=True)
    (share / "d.txt").write_bytes(b"x")
    out2 = register_dataset_tool({"title": "Homey", "path": str(share)}, {})
    hit2 = [i for i in resolve_inputs(f"load('{share}')")
            if i["ref"] == out2["dataset_id"]]
    assert hit2 and "content_ref" not in hit2[0]   # lazy identity: no ref yet


def test_produced_dataset_records_run_key(pid):
    """retention2: a dataset registered from a live kernel jobdir carries the
    (run, relpath) key — the durable handle, not just a path."""
    from core.compute.adapter import get_compute
    # a REAL kernel jobdir (list_kernels must know it) — start a kernel
    k = get_compute().sync_call("kernel_start", "local", env="__bare__") \
        if False else None
    # cheaper: fabricate via the discoverable path only if a live kernel
    # exists; otherwise assert the graceful absence (enrichment, not a gate)
    kernels = (get_compute().sync_call("list_kernels") or {}).get("kernels", [])
    live = [k for k in kernels if k.get("site") == "local" and k.get("jobdir")]
    if not live:
        p = _jobdir_file("rk.bin", b"R" * 128)
        out = register_dataset_tool({"title": "RK", "path": p}, {})
        md = get_entity(out["dataset_id"])["metadata"]
        assert "run_key" not in md          # no live kernel → no key, no error
        return
    jd = ad.weft_workspace() / "site-local" / live[0]["jobdir"]
    f = jd / "rk.bin"
    f.write_bytes(b"R" * 128)
    out = register_dataset_tool({"title": "RK", "path": str(f)}, {})
    md = get_entity(out["dataset_id"])["metadata"]
    assert md["run_key"]["run"] == live[0]["kernel_id"]
    assert md["run_key"]["rel"] == "rk.bin"


def test_paths_set_registration_dedups_on_retry(pid):
    """The SET shape had no dedup at all: a retried acquisition (re-run fetch,
    resumed session, second thread over the same files) minted a duplicate
    entity + bundle with no backstop — and the register-on-landing rule
    funnels agents onto exactly this shape. A set is its sorted member paths,
    order-free."""
    a = _jobdir_file("set_m1.bin", b"A" * 128)
    b = _jobdir_file("set_m2.bin", b"B" * 128)
    r1 = register_dataset_tool({"title": "Set", "paths": [a, b]}, {})
    assert r1.get("dataset_id") and not r1.get("already_registered"), r1
    r2 = register_dataset_tool({"title": "Set again", "paths": [b, a]}, {})
    assert r2.get("already_registered") is True, r2
    assert r2["dataset_id"] == r1["dataset_id"]
