"""Live smoke — R-3.5 advisor-shaped one-shot under AgentSDKRuntime.

What it proves:
  - Real advisor system prompts (methodologist + skeptic) generate
    text via AgentSDKRuntime end-to-end:
        seeded history (just the user prompt) → no tools → no halts
        → TextDelta stream → TurnDone.
  - oauth_cc credential mode + CC marker round-trip works for Haiku
    in the advisor configuration.
  - The 'no tools, no halts' degenerate path is well-behaved (no
    unintended ToolUseStart from CC defaults; disallowed_tools holds).

WHY not flipping production YAMLs yet: `run_advisor_one_shot` in
core/runtime/agent.py calls `anthropic.Anthropic` directly and does
not yet route through `make_runtime`. The runtime field on advisor
specs is decorative until that wire-up lands. This smoke validates
the *runtime path* so that wire-up can land confidently in a follow-
up commit.

Cost: ~$0.02 (two Haiku turns).
Run: .venv/bin/python tests/e2e/sdk_runtime_advisor_smoke.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TMP = Path(tempfile.mkdtemp(prefix="aba_sdk_advisor_"))
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
os.environ.setdefault("ABA_DB_PATH", str(_TMP / "test.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(_TMP / "artifacts"))
os.environ.setdefault("ABA_WORK_DIR", str(_TMP / "work"))
os.environ.setdefault("DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("ABA_RUNTIME_DIR", str(_TMP))
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.agent import get_agent_spec        # noqa: E402
from core.runtime.llm_runtime import (               # noqa: E402
    RuntimeRequest, SystemSpec, TextDelta,
    ToolUseStart, ToolResult, TurnDone, TurnHalt,
)
from core.runtime.llm_runtime_sdk import AgentSDKRuntime  # noqa: E402
import content.bio  # noqa: F401,E402 — registers advisor specs


METHODOLOGIST_FAKE_CODE = """\
Analysis: scRNA QC + clustering of PBMC

Producing code:
```python
import scanpy as sc
adata = sc.read_h5ad('pbmc.h5ad')
sc.pp.normalize_total(adata)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, flavor='seurat_v3', n_top_genes=2000)
sc.pp.scale(adata, max_value=10)
sc.tl.pca(adata)
sc.pp.neighbors(adata, n_neighbors=15)
sc.tl.leiden(adata, resolution=1.0)
sc.tl.umap(adata)
```

Review the methodology. What's the most important thing to check?"""


SKEPTIC_FAKE_RESULT = """\
## The result under review
Title: T-cell exhaustion drives poor immunotherapy response
Interpretation (verbatim from the user): The 12% increase in PD1+ cells \
in non-responders relative to responders demonstrates that T-cell exhaustion \
is the dominant mechanism of immunotherapy resistance in this cohort.

Supporting figure: pd1_violin.png (PD1 expression across responder groups).
Came from: analysis 'PD1 expression comparison'.

Review this result. What's the most important concern that would make a \
careful reviewer pause? Keep it to 3-5 sentences."""


async def _run_advisor(spec_name: str, user_prompt: str, *, max_tokens: int = 400):
    spec = get_agent_spec(spec_name)
    if spec is None:
        return None, [f"{spec_name!r} not registered"]

    req = RuntimeRequest(
        history=[{"role": "user", "content": user_prompt}],
        tools=[],
        system=SystemSpec(stable=spec.system_prompt, dynamic=""),
        model=spec.model,
        max_tokens=max_tokens,
        ctx={},
    )

    rt = AgentSDKRuntime()
    chunks: list[str] = []
    events: list = []

    async def _no_tools(name, args, ctx):
        return {"error": f"advisor {spec_name!r} should not call tools "
                          f"(called {name!r})"}

    async for ev in rt.run_turn(req, _no_tools):
        events.append(ev)
        if isinstance(ev, TextDelta):
            chunks.append(ev.text)

    text = "".join(chunks).strip()

    fails: list[str] = []
    tool_uses = [e for e in events if isinstance(e, ToolUseStart)]
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    halts = [e for e in events if isinstance(e, TurnHalt)]
    dones = [e for e in events if isinstance(e, TurnDone)]

    if tool_uses:
        fails.append(f"advisor reached for a tool: "
                     f"{[t.tool_name for t in tool_uses]!r}")
    if tool_results:
        fails.append(f"ToolResult unexpectedly fired")
    if halts:
        fails.append(f"TurnHalt fired (advisor should be no-halt): "
                     f"{halts[0].reason!r}")
    if not dones:
        fails.append("no TurnDone — turn did not finish cleanly")
    if not text:
        fails.append("advisor returned empty text")
    elif len(text) < 50:
        fails.append(f"advisor text suspiciously short: {text!r}")
    return text, fails


async def main():
    print("AgentSDKRuntime R-3.5 advisor smoke — Haiku via oauth_cc")
    print()

    print("── methodologist ────────────────────────────────────────")
    m_text, m_fails = await _run_advisor("methodologist", METHODOLOGIST_FAKE_CODE)
    if m_text:
        preview = m_text if len(m_text) < 280 else m_text[:280] + "…"
        print(f"  text: {preview}")
    print()
    print("── skeptic ──────────────────────────────────────────────")
    s_text, s_fails = await _run_advisor("skeptic", SKEPTIC_FAKE_RESULT)
    if s_text:
        preview = s_text if len(s_text) < 280 else s_text[:280] + "…"
        print(f"  text: {preview}")
    print()

    all_fails = [("methodologist", f) for f in m_fails] + \
                [("skeptic", f) for f in s_fails]
    if all_fails:
        print("FAIL:")
        for label, f in all_fails:
            print(f"  [{label}] {f}")
        sys.exit(1)
    print("OK — both advisors generate clean one-shot text under SDK runtime")


if __name__ == "__main__":
    asyncio.run(main())
