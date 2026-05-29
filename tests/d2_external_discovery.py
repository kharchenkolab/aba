"""
Item 3 — external discovery + live MCP adoption.

  1. search_nf_core / search_mcp_registry: BM25-rank a (stubbed) registry
     response; graceful on network failure.
  2. propose_capability adopts mcp_server / pipeline archetypes into the catalog.
  3. ensure_capability(mcp_server) connects a real stub stdio MCP server live;
     its tool becomes callable as 'server:tool'.

The two searches stub the HTTP layer (no network). The live-adopt path spawns a
real stub server (tests/fixtures/stub_mcp_server.py) via the .venv python.
Run:
    .venv/bin/python tests/d2_external_discovery.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d2_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d2.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                # noqa: E402
import content.bio  # noqa: E402,F401
import content.bio.tools as tools                      # noqa: E402
from content.bio.tools import (                        # noqa: E402
    search_nf_core, search_mcp_registry, propose_capability_tool, ensure_capability,
)
from core.catalog import resolve_capability            # noqa: E402
from core.runtime.mcp import is_mcp_tool, call, status, _reset_for_testing  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


# ---- fixtures ----
_NFCORE = {"remote_workflows": [
    {"name": "rnaseq", "description": "RNA sequencing analysis pipeline: alignment and quantification",
     "topics": ["rna-seq", "expression"], "releases": [{"tag_name": "3.14.0"}]},
    {"name": "sarek", "description": "Germline and somatic variant calling from WGS/WES",
     "topics": ["variant-calling", "dna"], "releases": [{"tag_name": "3.4.0"}]},
    {"name": "atacseq", "description": "ATAC-seq peak calling and chromatin accessibility",
     "topics": ["atac-seq", "epigenomics"], "releases": []},
]}
_MCP = {"servers": [
    {"name": "io.github.x/genomics-tools", "description": "Query genomic variants and gene annotations",
     "packages": [{"registry_name": "pypi", "name": "genomics-mcp"}], "repository": {"url": "https://github.com/x/genomics"}},
    {"name": "io.github.y/weather", "description": "Current weather and forecasts by city",
     "packages": [{"registry_name": "npm", "name": "@y/weather-mcp"}]},
]}


def test_search_nf_core():
    print("search_nf_core (stubbed registry)")
    tools._HTTP_GET_JSON = lambda url, timeout=15: _NFCORE
    res = search_nf_core({"query": "variant calling from sequencing"})
    names = [p["name"] for p in res.get("pipelines", [])]
    check("ranks sarek first for variant calling", names and names[0] == "sarek", str(names))
    check("returns url + release", res["pipelines"][0]["url"].endswith("/sarek")
          and res["pipelines"][0]["latest_release"] == "3.4.0")
    # graceful failure
    def boom(url, timeout=15):
        raise OSError("no network")
    tools._HTTP_GET_JSON = boom
    check("graceful on network failure", search_nf_core({"query": "x"}).get("status") == "error")


def test_search_mcp_registry():
    print("search_mcp_registry (stubbed registry)")
    tools._HTTP_GET_JSON = lambda url, timeout=15: _MCP
    res = search_mcp_registry({"query": "genomic variants and gene annotation"})
    servers = res.get("servers", [])
    check("ranks the genomics server first", servers and "genomics" in servers[0]["name"], str([s["name"] for s in servers]))
    check("derives a connection hint (pypi -> uvx)",
          servers[0]["connection"] == {"command": "uvx", "args": ["genomics-mcp"]}, str(servers[0]["connection"]))
    check("marks adoptable", servers[0]["adoptable"] is True)


def test_propose_archetypes():
    print("propose_capability: mcp_server + pipeline")
    r1 = propose_capability_tool({"name": "sarek", "archetype": "pipeline", "tags": ["variant-calling"]})
    check("pipeline approved", r1.get("status") == "approved" and r1.get("archetype") == "pipeline", str(r1))
    cap = resolve_capability("sarek")
    check("pipeline provisioning stored", (cap.get("provisioning") or {}).get("pipeline", {}).get("nf_core") == "sarek", str(cap))
    # ensure on a pipeline now installs nextflow (see d5_nextflow); mock the
    # materialize so this discovery test doesn't trigger a real conda install.
    from core.exec import MaterializingExecutor
    _orig = MaterializingExecutor.materialize
    MaterializingExecutor.materialize = lambda self, prov, scope="system", **kw: None
    try:
        check("ensure pipeline -> ready (nextflow)", ensure_capability({"name": "sarek"}).get("status") == "ready")
    finally:
        MaterializingExecutor.materialize = _orig

    r2 = propose_capability_tool({"name": "weather-mcp", "archetype": "mcp_server",
                                  "connection": {"command": "node", "args": ["x.js"]}})
    check("mcp_server approved", r2.get("status") == "approved" and r2.get("archetype") == "mcp_server", str(r2))
    cap2 = resolve_capability("weather-mcp")
    check("mcp connection stored", (cap2.get("provisioning") or {}).get("mcp_server", {}).get("command") == "node", str(cap2))
    # missing connection -> error
    r3 = propose_capability_tool({"name": "bad-mcp", "archetype": "mcp_server"})
    check("mcp_server without connection rejected", r3.get("status") == "error", str(r3))


def test_live_mcp_adoption():
    print("ensure_capability(mcp_server) -> live connect a real stub server")
    stub = ROOT / "tests" / "fixtures" / "stub_mcp_server.py"
    # Adopt a stub stdio server run by THIS python (mcp lib available here).
    propose_capability_tool({"name": "stub", "archetype": "mcp_server",
                             "connection": {"command": sys.executable, "args": [str(stub)]}})
    res = ensure_capability({"name": "stub"})
    check("ensure connected", res.get("status") == "ready", str(res))
    check("exposes stub:echo", "stub:echo" in (res.get("tools") or []), str(res.get("tools")))
    check("gateway sees the tool", is_mcp_tool("stub:echo"))
    out = call("stub:echo", {"text": "hi"})
    check("the adopted tool is callable", out.get("status") == "ok" and "echo: hi" in (out.get("content") or ""), str(out))
    st = status()
    check("status shows a connected server", any(s["state"] == "connected" for s in st["servers"]), str(st))
    _reset_for_testing()


def main() -> int:
    init_db()
    try:
        test_search_nf_core()
        test_search_mcp_registry()
        test_propose_archetypes()
        test_live_mcp_adoption()
    finally:
        try:
            _reset_for_testing()
        except Exception:
            pass
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL EXTERNAL-DISCOVERY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
