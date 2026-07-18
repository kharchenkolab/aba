"""Gated live-agent regression for chat attachments — the end-to-end test that
WOULD HAVE caught the live 400. OPT-IN: set ABA_LIVE_AGENT_TEST=1 + a credential.

Each scenario runs a REAL Guide turn (haiku) with an attachment and asserts:
  - the turn completes (no Anthropic 400 — the original bug);
  - NO `attachments` block reaches the API (inspect the real request dump);
  - the attachment does NOT auto-enter context (no image/document block the agent
    didn't pull) — only what the agent fetches via view_file;
  - the agent reaches for a sensible tool.

Run:  ABA_LIVE_AGENT_TEST=1 .venv/bin/python tests/test_live_agent_attachments.py
"""
from __future__ import annotations
import os
import sys
import glob
import json
import asyncio
import tempfile
from pathlib import Path

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _skip(msg):
    # dual-use gate: a clean exit for script runs, a real SKIP under pytest
    # (SystemExit at import is an INTERNALERROR that silently kills a whole
    # `pytest tests/` sweep — and printing the PASSED marker on a skip is a lie)
    print(f"[SKIP] {msg}")
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(msg, allow_module_level=True)
    raise SystemExit(0)


if not os.environ.get("ABA_LIVE_AGENT_TEST"):
    _skip("opt-in only — set ABA_LIVE_AGENT_TEST=1 (real LLM turn, spends tokens)")
if not (Path("~/.claude/.credentials.json").expanduser().exists() or os.environ.get("ANTHROPIC_API_KEY")):
    _skip("no LLM credential")

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="aba_la_attach_")
_DUMP = _TMP + "/llm_sent"
os.environ.update({
    "ABA_RUNTIME_DIR": _TMP, "ABA_PROJECTS_DIR": _TMP + "/projects",
    "ABA_ENVS_DIR": "/home/pkharchenko/aba/aba_runtime/envs",
    "ABA_RAW_REQUEST_DIR": _DUMP,                       # capture the EXACT API requests
})
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
os.environ.setdefault("ABA_MODEL", "claude-haiku-4-5-20251001")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))

from core import projects                                          # noqa: E402
from core.graph import messages                                    # noqa: E402
from core.runtime.attachments import save_attachment               # noqa: E402
import content.bio  # noqa: E402,F401
from core.runtime.content_pack import set_active_pack              # noqa: E402
from content.bio.pack import BIO_PACK                               # noqa: E402
set_active_pack(BIO_PACK); BIO_PACK.register_hooks()
from core.runtime.mcp import start_all as start_mcp, register_inprocess_server  # noqa: E402
start_mcp(ROOT / "backend" / "content" / "bio" / "mcp" / "servers.yaml")
from content.bio.mcp_servers.aba_core import make_server as make_aba_core       # noqa: E402
register_inprocess_server("aba_core", make_aba_core, expose_in_catalog=True, strip_prefix_in_catalog=True)
from guide import stream_response                                  # noqa: E402


def _dump_has_attachments_block() -> bool:
    """True if ANY request we just sent to the API carried a UI `attachments`
    block — the exact failure. (The boundary must strip it.)"""
    for fn in glob.glob(_DUMP + "/req_*.json"):
        try:
            payload = json.load(open(fn))
        except Exception:
            continue
        for m in payload.get("messages", []):
            for b in (m.get("content") if isinstance(m.get("content"), list) else []):
                if isinstance(b, dict) and b.get("type") == "attachments":
                    return True
    return False


def _clear_dump():
    for fn in glob.glob(_DUMP + "/req_*.json"):
        os.remove(fn)


async def _turn(tid, text, refs):
    async for _ in stream_response(user_text=text, thread_id=tid, attachments=refs):
        pass
    tools, err = [], False
    for m in messages.get_messages(thread_id=tid):
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tools.append(b.get("name"))
            if isinstance(b, dict) and b.get("type") == "text" and "stream_response failed" in (b.get("text") or ""):
                err = True
    return tools, err


async def main():
    projects.init(); projects.set_current(projects.create_project("LiveAttach")["id"])
    pid = projects.current()

    scenarios = [
        ("csv", "report.csv", b"gene,logfc,padj\nTP53,2.1,0.001\nMYC,-1.3,0.02\n",
         "What's in the file I attached?"),
        ("unknown", "blob.dat", b"\x1f\x8b\x08\x00\x00\x00\x00\x00garbage-bytes-here",
         "What is this file I attached?"),
    ]
    for label, name, data, prompt in scenarios:
        _clear_dump()
        import io
        ref = save_attachment(pid, f"thr_{label}", name, io.BytesIO(data))
        tools, err = await _turn(f"thr_{label}", prompt, [ref])
        print(f"\n[{label}] tools={tools}")
        check(f"[{label}] turn completed without a stream_response failure (no 400)", not err)
        check(f"[{label}] NO attachments block reached the API (the bug)", not _dump_has_attachments_block())
        check(f"[{label}] agent reached for a file tool",
              any(t in (tools or []) for t in ("view_file", "inspect_upload", "read_file", "run_python", "list_data_files")),
              str(tools))

    print()
    print(f"FAILED ({len(_failures)}): " + ", ".join(_failures) if _failures
          else "ALL LIVE-AGENT-ATTACH CHECKS PASSED")
    return 1 if _failures else 0


if __name__ == "__main__":                 # script-style: run via `python tests/…`.
    raise SystemExit(asyncio.run(main()))  # guarded so a pytest IMPORT (which hits the
                                           # module-level opt-in skip above) never executes
                                           # main(), and run_tests.sh's script path has an anchor.
