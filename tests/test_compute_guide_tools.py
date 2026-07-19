"""Guide compute-site tools (misc/compute_settings.md §9) — behavioral
guards, not just registration: the no-password contract, the explicit
host-key consent, and the connect confirmation gate."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_guide_compute_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.bio

from mcp.server.fastmcp import FastMCP  # noqa: E402
from content.bio.mcp_servers.aba_core.tools.compute_sites import (  # noqa: E402
    register_compute_sites_tools)


class FakePort:
    def sync_call(self, name, *a, **kw):
        assert name == "sites_list"
        return [{"name": "local", "kind": "local", "health": "ok"}]

    async def register_site(self, name, kind, config, **kw):
        self._last = (name, kind, config, kw)
        return {"site": name, "capabilities": {
            "cpus": 64, "mem_gb": 256, "gpus": [], "scheduler": {"type": "none"},
            "storage": {"candidates": [
                {"path": "/scratch/me", "writable": True, "free_gb": 100}]}}}


@pytest.fixture()
def tools(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "home"))
    port = FakePort()
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: port)
    mcp = FastMCP(name="t")
    register_compute_sites_tools(mcp)

    def call(name, **kw):
        # production runs tools on the tool-executor WORKER thread (run_sync
        # is worker-thread-only by design) — emulate that here
        import threading
        box: dict = {}

        def _run():
            try:
                out = asyncio.run(mcp.call_tool(name, kw))
                box["v"] = out[0] if isinstance(out, tuple) else out
            except BaseException as e:  # noqa: BLE001
                box["e"] = e
        t = threading.Thread(target=_run)
        t.start()
        t.join()
        if "e" in box:
            raise box["e"]
        return json.loads(box["v"][0].text)
    return call, port


def test_tools_registered():
    mcp = FastMCP(name="t")
    register_compute_sites_tools(mcp)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"list_compute_sites", "probe_compute_site",
                     "connect_compute_site", "data_safety_summary"}


def test_list_merges_aba_keys(tools):
    call, _ = tools
    out = call("list_compute_sites")
    assert out["sites"][0]["name"] == "local"
    assert out["sites"][0]["aba"]["contract"] == "shared-fs"


def test_probe_auth_returns_keysetup_never_asks_password(tools, monkeypatch):
    call, _ = tools
    from core.compute import preflight as pf
    monkeypatch.setattr(pf, "preflight",
                        lambda dest, port=None, ssh_opts=None:
                        {"case": "auth", "cause": "needs a password"})
    monkeypatch.setattr(pf, "keysetup",
                        lambda dest, port=None:
                        {"ok": True, "command": f"ssh-copy-id -i k {dest}"})
    out = call("probe_compute_site", dest="me@x.edu")
    assert out["case"] == "auth"
    assert out["keysetup"]["command"].startswith("ssh-copy-id")
    assert "own" in out["next"] and "password" in out["next"]
    assert "password" not in json.dumps(out["keysetup"])


def test_probe_hostkey_needs_explicit_consent(tools, monkeypatch):
    call, _ = tools
    from core.compute import preflight as pf
    accepted: list[str] = []
    monkeypatch.setattr(pf, "preflight",
                        lambda dest, port=None, ssh_opts=None:
                        {"case": "hostkey",
                         "hostkey": {"line": "h ed25519 AAAA",
                                     "fingerprint": "SHA256:abc",
                                     "keytype": "ed25519"}})
    monkeypatch.setattr(pf, "accept_hostkey", lambda line: accepted.append(line))
    out = call("probe_compute_site", dest="me@x.edu")
    assert out["case"] == "hostkey" and not accepted   # shown, NOT accepted
    assert out["hostkey"]["fingerprint"] == "SHA256:abc"
    call("probe_compute_site", dest="me@x.edu", accept_hostkey=True)
    assert accepted == ["h ed25519 AAAA"]              # only with consent


def test_probe_ok_returns_proposal(tools, monkeypatch):
    call, port = tools
    from core.compute import preflight as pf
    monkeypatch.setattr(pf, "preflight",
                        lambda dest, port=None, ssh_opts=None: {"case": "ok"})
    monkeypatch.setattr(pf, "remote_facts",
                        lambda *a, **k: {"ok": True, "present": [],
                                         "scheduler": "none", "accounts": []})
    out = call("probe_compute_site", dest="me@files.lab.edu")
    assert out["case"] == "ok"
    p = out["proposal"]
    assert p["kind"] == "ssh" and p["name"] == "files"
    assert p["working"]["root"] == "/scratch/me/.weft"
    # probe_only went through the port
    assert port._last[3] == {"probe_only": True}


def test_self_service_disabled_blocks_probe_and_connect(tools, monkeypatch):
    """A shared deployment (ABA_COMPUTE_SELF_SERVICE=false) manages its own
    machines: the Guide's add-a-site tools refuse, matching the tab/API 403, so
    the agent can't add a node behind the read-only UI's back. Reads still work."""
    call, port = tools
    from core.compute import sites_config
    monkeypatch.setattr(sites_config, "self_service", lambda: False)
    out = call("probe_compute_site", dest="me@x.edu")
    assert out["error"] == "self_service_disabled"
    proposal = {"name": "box", "kind": "ssh", "use_for": ["background"],
                "working": {"root": "/scratch/me/.weft"},
                "long_term": [], "contract": "detached", "partitions": []}
    out = call("connect_compute_site", dest="me@box", proposal=proposal,
               confirmed=True)
    assert out["error"] == "self_service_disabled"
    assert not hasattr(port, "_last")            # nothing was registered
    assert call("list_compute_sites")["sites"][0]["name"] == "local"  # reads OK


def test_connect_refuses_without_confirmation(tools):
    call, port = tools
    proposal = {"name": "box", "kind": "ssh", "use_for": ["background"],
                "working": {"root": "/scratch/me/.weft"},
                "long_term": [], "contract": "detached", "partitions": []}
    out = call("connect_compute_site", dest="me@box", proposal=proposal)
    assert out["error"] == "user_confirmation_required"
    out = call("connect_compute_site", dest="me@box", proposal=proposal,
               confirmed=True)
    assert out["site"] == "box"
    from core.compute import sites_config
    assert sites_config.aba_keys("box")["use_for"] == ["background"]
