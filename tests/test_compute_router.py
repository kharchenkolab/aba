"""/api/compute router (misc/compute_settings.md §7) — endpoint behavior over
a fake SitePort; ssh helpers monkeypatched (no network, no weft install)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_compute_router_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core.compute.errors import ComputeError  # noqa: E402
from core.web.routers import compute as cr  # noqa: E402


def slurm_caps() -> dict:
    return {
        "schema": "capabilities:v2", "cpus": 16, "mem_gb": 64,
        "internet": True, "module_system": True, "gpus": [],
        "scheduler": {"type": "slurm", "version": "23.02", "partitions": [
            {"name": "cpu", "cpus_per_node": 64, "mem_gb_per_node": 256,
             "nodes": 400, "available": True, "gres": [],
             "max_walltime": "14-00:00:00"},
            {"name": "gpu", "cpus_per_node": 128, "mem_gb_per_node": 1024,
             "nodes": 11, "available": True,
             "gres": [{"type": "gpu", "model": "a100", "count": 8}],
             "max_walltime": "2-00:00:00"}]},
        "storage": {"free_gb": 40, "candidates": [
            {"path": "/scratch/me", "writable": True, "free_gb": 4300}]},
    }


class FakeSitePort:
    """Successful-path fake; individual tests override methods."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.sites: dict[str, dict] = {
            "local": {"name": "local", "kind": "local", "health": "ok",
                      "cpus": 8, "mem_gb": 32, "gpus": 0,
                      "scheduler": "none", "internet": True,
                      "config": {"root": "/x"}, "capabilities": {"cpus": 8}}}

    async def sites_list(self):
        self.calls.append(("sites_list",))
        return [dict(v) for v in self.sites.values()]

    async def sites_describe(self, name):
        self.calls.append(("sites_describe", name))
        if name not in self.sites:
            raise ComputeError("site.unknown", f"unknown site {name}")
        return dict(self.sites[name])

    async def register_site(self, name, kind, config, **kw):
        self.calls.append(("register_site", name, kind, config, kw))
        caps = slurm_caps() if kind == "slurm" else {"cpus": 64}
        if kw.get("probe_only"):
            return {"site": name, "probe_only": True, "capabilities": caps}
        self.sites[name] = {"name": name, "kind": kind, "config": config,
                            "capabilities": caps, "health": "ok"}
        return {"site": name, "capabilities": caps}

    async def site_probe(self, name):
        self.calls.append(("site_probe", name))
        return {"cpus": 16}

    async def site_probe_deep(self, name, partitions=None, **kw):
        self.calls.append(("site_probe_deep", name, tuple(partitions or ())))
        return {"site": name, "partitions": {
            p: {"ok": p != "broken"} for p in (partitions or ["cpu"])}}

    async def site_load(self, name, resources=None, **kw):
        return {"site": name, "start_estimate": "now" if resources else None}

    async def site_footprint(self, site):
        return {"site": site, "free_bytes": 10, "prefixes_bytes": 5}

    async def site_unregister(self, name):
        self.calls.append(("site_unregister", name))
        if name == "busy":
            raise ComputeError("state.conflict", "jobs running",
                               hints={"jobs": ["jb_1"]})
        self.sites.pop(name, None)
        return {"site": name, "unregistered": True}

    async def gc_plan(self, site=None):
        return {"site": site, "reclaimable_bytes": 999}

    async def gc_sweep(self, site, confirm=False):
        assert confirm is True
        return {"site": site, "freed_bytes": 999}


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "home"))
    port = FakeSitePort()
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: port)
    monkeypatch.setattr(cr, "_broadcast", lambda *a, **k: None)
    cr._verify_tasks.clear()
    cr._verify_state.clear()
    return port


@pytest.fixture()
def client(fake):
    app = FastAPI()
    app.include_router(cr.router)
    with TestClient(app) as c:
        yield c


# ── list / detail ────────────────────────────────────────────────────────────

def test_list_merges_aba_keys(client, fake):
    from core.compute import sites_config
    sites_config.upsert_site("vbc", "slurm", {"root": "/s"},
                             aba={"contract": "shared-fs", "use_for": ["gpu"]})
    fake.sites["vbc"] = {"name": "vbc", "kind": "slurm", "health": "ok",
                         "config": {"root": "/s"}, "capabilities": {}}
    r = client.get("/api/compute/sites").json()
    by = {s["name"]: s for s in r["sites"]}
    assert by["vbc"]["aba"]["use_for"] == ["gpu"]
    assert by["local"]["aba"]["contract"] == "shared-fs"   # implicit default


def test_detail_and_unknown(client):
    assert client.get("/api/compute/sites/local").status_code == 200
    assert client.get("/api/compute/sites/nope").status_code == 502


def test_offline_is_503(client, monkeypatch):
    import core.compute.adapter as ad

    def down():
        raise ComputeError("substrate_offline", "no pixi")
    monkeypatch.setattr(ad, "get_compute", down)
    assert client.get("/api/compute/sites").status_code == 503


# ── connect flow ─────────────────────────────────────────────────────────────

def test_probe_returns_proposal(client, fake, monkeypatch):
    from core.compute import preflight as pf
    monkeypatch.setattr(pf, "remote_facts",
                        lambda *a, **k: {"ok": True, "present": ["/groups/lab"],
                                         "scheduler": "slurm",
                                         "accounts": ["lab-alloc"]})
    r = client.post("/api/compute/probe",
                    json={"dest": "me@login.vbc.ac.at"})
    assert r.status_code == 200, r.text
    body = r.json()
    p = body["proposal"]
    assert p["kind"] == "slurm" and p["name"] == "vbc"
    assert p["contract"] == "shared-fs"
    assert p["account"] == "lab-alloc"
    assert p["working"]["root"] == "/scratch/me/.weft"
    # probe_only went through the port, nothing was persisted
    kinds = [c[0] for c in fake.calls]
    assert "register_site" in kinds and "vbc" not in fake.sites


def test_connect_registers_writes_yaml_and_verifies(client, fake):
    from core.compute import sites_config
    proposal = {
        "name": "vbc", "kind": "slurm",
        "use_for": ["interactive", "background", "gpu"],
        "working": {"root": "/scratch/me/.weft"},
        "long_term": [{"path": "/groups/lab", "stable": True}],
        "contract": "shared-fs",
        "partitions": [{"name": "cpu", "selected": True},
                       {"name": "gpu", "selected": True}],
        "account": "lab-alloc"}
    r = client.post("/api/compute/sites",
                    json={"dest": "me@login.vbc.ac.at", "proposal": proposal})
    assert r.status_code == 200, r.text
    assert r.json()["verifying"] is True
    # weft got the register with policy roles + account
    call = next(c for c in fake.calls if c[0] == "register_site")
    cfg = call[3]
    assert cfg["host"] == "login.vbc.ac.at" and cfg["user"] == "me"
    assert cfg["policy"]["storage"]["large"] == "/groups/lab"
    assert cfg["scheduler"] == {"account": "lab-alloc"}
    # the YAML mirror carries the aba keys
    aba = sites_config.aba_keys("vbc")
    assert aba["contract"] == "shared-fs"
    assert aba["storage"] == [{"path": "/groups/lab", "stable": True}]
    # background verify ran (TestClient drains the loop on exit)


def test_verify_endpoint_records_outcome(client, fake):
    fake.sites["vbc"] = {"name": "vbc", "kind": "slurm",
                         "config": {"policy": {"partitions_allowed":
                                               ["cpu", "broken"]}},
                         "capabilities": {}}
    r = client.post("/api/compute/sites/vbc/verify")
    assert r.status_code == 200 and r.json()["started"]
    # TestClient runs the loop to completion on request return? Not for
    # created tasks — poll the recorded state via the detail endpoint.
    import time
    for _ in range(50):
        st = cr._verify_state.get("vbc", {})
        if st.get("state") == "done":
            break
        time.sleep(0.05)
    assert st["state"] == "done"
    assert st["ok"] is False and st["failed"] == ["broken"]


def test_reprobe(client, fake):
    r = client.post("/api/compute/sites/local/reprobe")
    assert r.status_code == 200
    assert ("site_probe", "local") in fake.calls


# ── access endpoints ─────────────────────────────────────────────────────────

def test_preflight_rejects_invalid(client):
    r = client.post("/api/compute/preflight", json={"dest": "host; rm -rf /"})
    assert r.status_code == 400


def test_keysetup_returns_command_and_never_a_password_field(client, monkeypatch):
    from core.compute import preflight as pf
    monkeypatch.setattr(pf, "keysetup",
                        lambda dest, port=None: {"ok": True, "created": False,
                                                 "command": f"ssh-copy-id -i k {dest}"})
    r = client.post("/api/compute/keysetup", json={"dest": "me@x.edu"})
    assert r.status_code == 200
    assert r.json()["command"].startswith("ssh-copy-id")
    # the request model has no secret-bearing field
    assert "password" not in cr.Target.model_fields


def test_hostkey_accept(client, monkeypatch, tmp_path):
    from core.compute import preflight as pf
    monkeypatch.setattr(pf, "known_hosts_path",
                        lambda: tmp_path / "kh")
    r = client.post("/api/compute/hostkey", json={"line": "h ssh-ed25519 AAAA"})
    assert r.status_code == 200
    assert (tmp_path / "kh").read_text().strip() == "h ssh-ed25519 AAAA"


# ── manage ───────────────────────────────────────────────────────────────────

def test_edit_updates_yaml_and_policy(client, fake):
    from core.compute import sites_config
    fake.sites["vbc"] = {"name": "vbc", "kind": "slurm",
                         "config": {"root": "/s", "host": "h"},
                         "capabilities": {}}
    r = client.patch("/api/compute/sites/vbc", json={
        "use_for": ["background"],
        "long_term": [{"path": "/groups/lab", "stable": True},
                      {"path": "/groups/other", "stable": True}]})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["policy"]["storage"]["large"] == "/groups/lab"
    aba = sites_config.aba_keys("vbc")
    assert aba["use_for"] == ["background"] and len(aba["storage"]) == 2


def test_disconnect_and_busy_409(client, fake):
    from core.compute import sites_config
    sites_config.upsert_site("gone", "ssh", {"root": "/g"})
    fake.sites["gone"] = {"name": "gone", "kind": "ssh", "config": {},
                          "capabilities": {}}
    r = client.delete("/api/compute/sites/gone")
    assert r.status_code == 200
    assert sites_config.aba_keys("gone") == {}      # YAML entry dropped
    fake.sites["busy"] = {"name": "busy", "kind": "ssh", "config": {},
                          "capabilities": {}}
    r = client.delete("/api/compute/sites/busy")
    assert r.status_code == 409
    assert r.json()["detail"]["hints"]["jobs"] == ["jb_1"]


def test_local_cannot_be_disconnected(client):
    assert client.delete("/api/compute/sites/local").status_code == 400


def test_gc_plan_then_sweep(client, fake):
    plan = client.post("/api/compute/sites/local/gc", json={"confirm": False})
    assert plan.json()["reclaimable_bytes"] == 999
    sweep = client.post("/api/compute/sites/local/gc", json={"confirm": True})
    assert sweep.json()["freed_bytes"] == 999


# ── Advanced ↗ (weft-ui mount, shared controller) ────────────────────────────

def test_advanced_reports_unavailable_without_mount(client, monkeypatch):
    from core.web import weftui
    monkeypatch.setitem(weftui._state, "available", False)
    r = client.get("/api/compute/advanced").json()
    assert r == {"available": False, "url": None}


def test_advanced_url_shape(client, monkeypatch):
    from core.web import weftui
    monkeypatch.setitem(weftui._state, "available", True)
    monkeypatch.setitem(weftui._state, "token", "tok123")
    r = client.get("/api/compute/advanced?site=vbc").json()
    assert r["available"] is True
    assert r["url"] == "/weft/?token=tok123&hide=chat#/compute/vbc"


def test_mount_degrades_when_substrate_offline(monkeypatch, tmp_path):
    """weftui.mount + an offline substrate: the host app must boot fine and
    the mount must serve nothing (weft-ui's degraded shared-controller mode)."""
    pytest.importorskip("weft_ui")
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "home"))
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "_adapter", None)
    monkeypatch.setattr(ad, "_status", {"ok": False, "severity": "warning",
                                        "detail": "offline for test"})
    from core.web import weftui
    monkeypatch.setattr(weftui, "_state", {"available": False, "token": None})
    app = FastAPI()
    assert weftui.mount(app) is True
    with TestClient(app) as c:          # boot survives the failing factory
        r = c.get("/weft/api/w", headers={"authorization": "Bearer "
                                          + (weftui._state["token"] or "")})
        assert r.status_code in (401, 404)   # no tool routers came up
    assert weftui.advanced_url() is not None  # mounted, even if degraded


# ── templates ────────────────────────────────────────────────────────────────

def test_templates_empty_then_declared(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "h2"))
    assert client.get("/api/compute/templates").json() == {"templates": []}
    (tmp_path / "h2").mkdir(parents=True, exist_ok=True)
    (tmp_path / "h2" / "compute-templates.yaml").write_text(
        "templates:\n  - name: VBC cluster\n    dest: login.vbc.ac.at\n"
        "    note: the lab's main cluster\n")
    out = client.get("/api/compute/templates").json()
    assert out["templates"][0]["name"] == "VBC cluster"
