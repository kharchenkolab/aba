"""Gated live-agent regression: a REAL Guide turn (haiku, real tool catalog)
operating the reference tools. OPT-IN — set ABA_LIVE_AGENT_TEST=1 and have a
working LLM credential. Otherwise it SKIPS (passes), so CI never spends tokens
or flakes on model nondeterminism.

Guards two things the live-agent passes caught (2026-06-28):
  - the agent's natural organism name hits stored refs (facet normalization);
  - the agent drives the full find -> fetch -> resolve loop in one turn.

Run:  ABA_LIVE_AGENT_TEST=1 .venv/bin/python tests/test_live_agent_refs.py
"""
from __future__ import annotations
import os
import sys
import asyncio
import tempfile
import json
from pathlib import Path

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _skip(msg):
    print(f"[SKIP] {msg}")
    print("ALL LIVE-AGENT-REFS CHECKS PASSED")
    raise SystemExit(0)


if not os.environ.get("ABA_LIVE_AGENT_TEST"):
    _skip("opt-in only — set ABA_LIVE_AGENT_TEST=1 (real LLM turn, spends tokens)")
if not (Path("~/.claude/.credentials.json").expanduser().exists()
        or Path("~/.aba/oauth.json").expanduser().exists()
        or os.environ.get("ANTHROPIC_API_KEY")):
    _skip("no LLM credential (~/.claude/.credentials.json | ~/.aba/oauth.json | ANTHROPIC_API_KEY)")

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="aba_liveagent_")
os.environ.pop("ABA_DB_PATH", None)
os.environ.pop("ABA_DB_PATH", None)
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
os.environ["ABA_ENVS_DIR"] = _TMP + "/envs"
os.environ["ABA_REFS_DIR"] = _TMP + "/refs"
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
os.environ.setdefault("ABA_MODEL", "claude-haiku-4-5-20251001")
_RS = Path(_TMP) / "refsources"
_RS.mkdir(parents=True, exist_ok=True)
(_RS / "test-phix.yaml").write_text(
    "provider: test-phix\nkind: manifest\nassets:\n  - role: genome\n    organism: phix\n"
    "    assembly: NC_001422\n    version: NC_001422.1\n"
    "    url: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    "?db=nuccore&id=NC_001422.1&rettype=fasta&retmode=text\n")
os.environ["ABA_REFSOURCES_DIR"] = str(_RS)
sys.path.insert(0, str(ROOT / "backend"))

from core import projects                                   # noqa: E402
from core.graph import messages                             # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import register_reference_tool       # noqa: E402
from core.runtime.content_pack import set_active_pack       # noqa: E402
from content.bio.pack import BIO_PACK                        # noqa: E402
set_active_pack(BIO_PACK); BIO_PACK.register_hooks()
from core.runtime.mcp import start_all as start_mcp, register_inprocess_server  # noqa: E402
start_mcp(ROOT / "backend" / "content" / "bio" / "mcp" / "servers.yaml")
from content.bio.mcp_servers.aba_core import make_server as make_aba_core       # noqa: E402
register_inprocess_server("aba_core", make_aba_core, expose_in_catalog=True,
                          strip_prefix_in_catalog=True)
from guide import stream_response                           # noqa: E402


def _calls_and_results(tid):
    calls, results = [], []
    for m in messages.get_messages(thread_id=tid):
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                calls.append(b.get("name"))
            elif b.get("type") == "tool_result":
                c = b.get("content")
                results.append(c if isinstance(c, str) else json.dumps(c))
    return calls, results


async def _turn(tid, prompt):
    async for _ in stream_response(user_text=prompt, thread_id=tid):
        pass
    return _calls_and_results(tid)


async def main():
    projects.init()
    projects.set_current(projects.create_project("LiveAgentRefs")["id"])
    seed = Path(_TMP) / "fly.fa"; seed.write_text(">chr2L\nACGTACGT\n")
    register_reference_tool({"path": str(seed), "organism": "drosophila_melanogaster",
                             "role": "genome", "assembly": "BDGP6", "scope": "personal"})

    print("[1] natural-name find hits a stored ref (facet normalization)")
    calls, results = await _turn(
        "thr_find", "Do we already have a Drosophila melanogaster genome reference? Check the store.")
    check("agent called find_reference", "find_reference" in calls, str(calls))
    check("find returned a hit on the agent's natural name",
          any('"found": true' in r for r in results), str(results)[:160])

    print("[2] full loop in one turn: find -> fetch -> resolve")
    calls, results = await _turn(
        "thr_loop",
        "Set up the phiX174 genome (assembly NC_001422) as a reference: check if we have it, "
        "fetch it from the 'test-phix' provider if not, and resolve it to a local path for the run.")
    print("    order:", [c for c in calls if c and c.endswith("_reference")])
    check("loop: find_reference used", "find_reference" in calls)
    check("loop: fetch_reference used + succeeded",
          "fetch_reference" in calls and any('"status": "ok"' in r for r in results), str(calls))
    check("loop: resolve_reference used", "resolve_reference" in calls, str(calls))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL LIVE-AGENT-REFS CHECKS PASSED")
    return 0


raise SystemExit(asyncio.run(main()))
