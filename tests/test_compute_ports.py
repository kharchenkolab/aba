"""Compute-port layer (weft rewrite W0.4 — misc/weft_rewrite.md §3).

Two guarantees:
  1. `core/compute` is aba's ONLY doorway to weft — an AST guard fails on any
     `import weft` outside the package (the compute-plane analog of the env
     registry's anti-bypass guard).
  2. The adapter actually works end-to-end against an embedded weft: local
     site registered, a trivial task runs to DONE, error payloads become
     ComputeError, all three port Protocols are satisfied. Skipped when the
     weft package isn't installed in the test environment.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"

weft_available = True
try:  # pragma: no cover - environment probe
    import weft.api  # noqa: F401
except Exception:  # noqa: BLE001
    weft_available = False


# ── 1. the doorway guard ─────────────────────────────────────────────────────

def test_only_core_compute_imports_weft():
    offenders = {}
    for py in sorted(BACKEND.rglob("*.py")):
        rel = str(py.relative_to(BACKEND))
        if rel.startswith("core/compute/"):
            continue
        try:
            tree = ast.parse(py.read_text(errors="replace"))
        except SyntaxError:
            continue
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hits += [(node.lineno, a.name) for a in node.names
                         if a.name == "weft" or a.name.startswith("weft.")]
            elif isinstance(node, ast.ImportFrom) and node.module \
                    and (node.module == "weft" or node.module.startswith("weft.")):
                hits.append((node.lineno, node.module))
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "weft may only be imported inside core/compute/ (the port layer). "
        "Offenders:\n"
        + "\n".join(f"  {f}: {hits}" for f, hits in offenders.items()))


# ── 2. adapter end-to-end (needs weft + pixi on the box) ─────────────────────

@pytest.fixture()
def adapter(tmp_path, monkeypatch):
    if not weft_available:
        pytest.skip("weft package not installed")
    from core.compute import adapter as mod
    pixi = mod.resolve_pixi()
    if pixi is None:
        pytest.skip("pixi binary not available")
    monkeypatch.setenv("ABA_WEFT_WORKSPACE", str(tmp_path / "weft-ws"))
    # fresh process-wide state for the test
    monkeypatch.setattr(mod, "_adapter", None)
    monkeypatch.setattr(mod, "_status", {"ok": False, "severity": "info",
                                         "detail": "unconfigured"})
    st = mod.configure()
    assert st["ok"], st["detail"]
    yield mod.get_compute()
    mod.shutdown()


def test_adapter_satisfies_all_three_ports(adapter):
    from core.compute.ports import EnvPort, RunPort, SitePort
    assert isinstance(adapter, SitePort)
    assert isinstance(adapter, EnvPort)
    assert isinstance(adapter, RunPort)


def test_every_port_method_is_a_real_weft_tool(adapter):
    """Drift guard against weft upstream: every method a port declares must be
    a same-named public weft tool. A weft rename fails HERE, loudly, instead of
    as a runtime AttributeError mid-turn."""
    from weft.api import Weft
    from core.compute.ports import EnvPort, RunPort, SitePort
    missing = []
    for port in (SitePort, EnvPort, RunPort):
        for name in getattr(port, "__protocol_attrs__", ()):
            target = getattr(Weft, name, None)
            if target is None or not getattr(target, "_weft_tool", False):
                missing.append(f"{port.__name__}.{name}")
    assert not missing, f"port methods with no matching weft tool: {missing}"


def test_adapter_runs_a_trivial_task(adapter):
    async def go():
        sites = await adapter.sites_list()
        assert any(s.get("name") == "local" for s in sites)
        d = await adapter.doctor()
        assert d["sites"] and d["sites"][0]["ok"]
        r = await adapter.task_submit({"command": "echo compute-port-ok",
                                       "site": "local", "label": "w0-smoke"})
        job_id = r["job_id"]
        for _ in range(60):
            st = (await adapter.task_status(job_id))[0]["state"]
            if st in ("DONE", "FAILED", "CANCELLED"):
                break
            await asyncio.sleep(0.5)
        assert st == "DONE"
        res = await adapter.task_result(job_id)
        assert res["exit_code"] == 0
        assert "compute-port-ok" in res["logs"]["tail"]
        # placement fact is already first-class (the §4d ask, delivered)
        assert res.get("node")
    asyncio.run(go())


def test_error_payloads_become_compute_error(adapter):
    from core.compute import ComputeError
    async def go():
        with pytest.raises(ComputeError) as ei:
            await adapter.task_result("jb_does_not_exist")
        assert ei.value.code
        assert ei.value.to_payload()["error"] == ei.value.code
    asyncio.run(go())


def test_non_tool_attributes_fail_loudly(adapter):
    with pytest.raises(AttributeError):
        adapter.not_a_weft_tool
    with pytest.raises(AttributeError):
        adapter.store  # weft internal, not a tool — must not leak through


def test_get_compute_raises_when_offline(monkeypatch):
    from core.compute import adapter as mod
    from core.compute import ComputeError
    monkeypatch.setattr(mod, "_adapter", None)
    monkeypatch.setattr(mod, "_status", {"ok": False, "severity": "warning",
                                         "detail": "offline for test"})
    with pytest.raises(ComputeError, match="offline for test"):
        mod.get_compute()
