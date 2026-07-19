"""Gated live-agent test: Guide surfaces the pagoda3 viewer for single-cell
results via `open_viewer`, and does NOT for non-single-cell files.

Runs REAL Guide turns and asserts, per scenario:
  - a single-cell result → the agent calls `open_viewer`, it returns ok:true with a
    /viewer-launch URL whose path RESOLVES (find_file_node), and the agent's reply
    carries that link (not a raw URL);
  - a plain table (.csv) → the agent does NOT hand out a viewer link (open_viewer
    either isn't called or returns ok:false).

Run:  ABA_LIVE_AGENT_TEST=1 ABA_MODEL=claude-haiku-4-5-20251001 \
      ~/.aba/env/bin/python tests/test_live_viewer_offer.py
"""
from __future__ import annotations
import os
import sys
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
if not (Path("~/.aba/oauth.json").expanduser().exists()
        or Path("~/.claude/.credentials.json").expanduser().exists()
        or os.environ.get("ANTHROPIC_API_KEY")):
    _skip("no LLM credential")

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="aba_live_viewer_")
os.environ.update({"ABA_RUNTIME_DIR": _TMP, "ABA_PROJECTS_DIR": _TMP + "/projects"})
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
os.environ.setdefault("ABA_MODEL", "claude-haiku-4-5-20251001")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))

from core import projects                                          # noqa: E402
from core.graph import messages                                    # noqa: E402
import content.bio  # noqa: E402,F401
from core.runtime.content_pack import set_active_pack              # noqa: E402
from content.bio.pack import BIO_PACK                               # noqa: E402
set_active_pack(BIO_PACK); BIO_PACK.register_hooks()
from core.runtime.mcp import start_all as start_mcp, register_inprocess_server  # noqa: E402
start_mcp(ROOT / "backend" / "content" / "bio" / "mcp" / "servers.yaml")
from content.bio.mcp_servers.aba_core import make_server as make_aba_core       # noqa: E402
register_inprocess_server("aba_core", make_aba_core, expose_in_catalog=True, strip_prefix_in_catalog=True)
from guide import stream_response                                  # noqa: E402
from core.config import project_data_dir                           # noqa: E402


def _make_h5ad(path: Path):
    import anndata as ad, numpy as np, pandas as pd, scipy.sparse as sp
    rng = np.random.default_rng(0)
    X = sp.random(200, 60, density=0.2, format="csr", dtype="float32", random_state=0)
    obs = pd.DataFrame({"leiden": pd.Categorical([str(i % 5) for i in range(200)])},
                       index=[f"cell{i}" for i in range(200)])
    var = pd.DataFrame(index=[f"g{i}" for i in range(60)])
    a = ad.AnnData(X=X, obs=obs, var=var)
    a.obsm["X_umap"] = rng.standard_normal((200, 2)).astype("float32")
    a.write_h5ad(path)


def _make_csv(path: Path):
    path.write_text("gene,logfc,padj\nTP53,2.1,0.001\nMYC,-1.3,0.02\n")


async def _turn(tid, text):
    async for _ in stream_response(user_text=text, thread_id=tid):
        pass
    tools, open_results, reply = [], [], []
    for m in messages.get_messages(thread_id=tid):
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                tools.append(b.get("name"))
            if b.get("type") == "tool_result":
                raw = b.get("content")
                txt = raw if isinstance(raw, str) else json.dumps(raw)
                if "viewer_url" in txt or "no external viewer" in txt or "no file matching" in txt:
                    open_results.append(txt)
            if b.get("type") == "text" and m.get("role") == "assistant":
                reply.append(b.get("text") or "")
    return tools, open_results, "\n".join(reply)


async def main():
    projects.init(); projects.set_current(projects.create_project("LiveViewer")["id"])
    pid = projects.current()
    data = project_data_dir(pid)
    _make_h5ad(data / "clustered.h5ad")
    _make_csv(data / "de_table.csv")

    # --- scenario 1: single-cell result → offer a resolving viewer link ---
    tools, results, reply = await _turn(
        "thr_sc",
        "I have a clustered single-cell result at data/clustered.h5ad. "
        "Open it in the pagoda3 viewer so I can explore the clusters.")
    print(f"\n[single-cell] tools={tools}")
    check("[sc] agent called open_viewer", "open_viewer" in tools, str(tools))
    ok = any("viewer_url" in r for r in results)
    check("[sc] open_viewer returned a viewer_url (ok:true)", ok, str(results)[:300])
    # the returned link's path resolves
    resolved = False
    for r in results:
        try:
            d = json.loads(r) if r.strip().startswith("{") else {}
        except Exception:
            d = {}
        url = d.get("viewer_url", "")
        if "/viewer-launch" in url:
            from urllib.parse import urlparse, parse_qs
            from content.bio.files.tree import build_files_tree, find_file_node
            q = parse_qs(urlparse(url).query)
            p = (q.get("path") or [""])[0]
            if p:
                resolved = find_file_node(build_files_tree(include_archived=False), p) is not None
    check("[sc] the viewer_url path resolves to a real file", resolved)
    check("[sc] agent's reply presents a /viewer-launch link", "/viewer-launch" in reply, reply[:200])

    # --- scenario 2: a plain table → NO viewer link ---
    tools2, results2, reply2 = await _turn(
        "thr_csv",
        "Open data/de_table.csv in the pagoda3 viewer.")
    print(f"[csv] tools={tools2}")
    no_link = "/viewer-launch" not in reply2
    declined = (not any("viewer_url" in r for r in results2))  # open_viewer, if called, returned no viewer
    check("[csv] agent did NOT hand out a viewer link for a non-single-cell file",
          no_link and declined, f"reply={reply2[:200]} results={str(results2)[:200]}")

    print()
    print(f"FAILED ({len(_failures)}): " + ", ".join(_failures) if _failures
          else "ALL LIVE-VIEWER-OFFER CHECKS PASSED")
    return 1 if _failures else 0


if __name__ == "__main__":                 # script-style: run via `python tests/…`.
    raise SystemExit(asyncio.run(main()))  # guarded so a pytest IMPORT (which hits the
                                           # module-level opt-in skip above) never executes
                                           # main(), and run_tests.sh's script path has an anchor.
