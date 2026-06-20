"""Phase 0 fixture dumper for the Qwen3 viability smoke test.

Dumps system prompt + OpenAI-compatible tool list for BOTH the full
"guide" spec and the lean "lean_guide" spec, plus a baseline of
recent Anthropic prompt-token counts. Prints a side-by-side savings
report — the headline number for lean-spec validation.

Writes per-spec under local-llm/phase0/fixtures/{guide,lean_guide}/:

  aba_system.txt            joined stable + dynamic system prompt
  aba_tools_anthropic.json  list_tools() raw output (FILTERED by allowlist)
  aba_tools_openai.json     same, converted to {type:function, function:{...}}

And at the top level:
  baseline.json             last ~10 input_tokens from runs.usage_blob

Usage:
    .venv/bin/python scripts/dump_phase0_fixture.py
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT  = ROOT.parent / "local-llm" / "phase0" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)

_tmp = tempfile.mkdtemp(prefix="aba_phase0_dump_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "phase0.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", str(_tmp))
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))

sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                                # noqa: E402
init_db()

import content.bio                                                    # noqa: F401,E402
from core.runtime.mcp import (                                         # noqa: E402
    register_inprocess_server, _reset_for_testing, list_tools,
)
from content.bio.mcp_servers.aba_core import make_server               # noqa: E402

_reset_for_testing()
register_inprocess_server(
    "aba_core", make_server,
    expose_in_catalog=True, strip_prefix_in_catalog=True,
)

from content.bio.prompts.build import build_system                     # noqa: E402
from core.runtime.agent import get_agent_spec, filter_tools_by_allowlist  # noqa: E402

INTENT = "list the data files in this project"


def to_openai(t: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name":        t["name"],
            "description": t.get("description") or "",
            "parameters":  t.get("input_schema") or {"type": "object"},
        },
    }


def dump_for_spec(spec_name: str) -> dict:
    spec = get_agent_spec(spec_name)
    if spec is None:
        raise RuntimeError(f"spec {spec_name!r} not registered")
    # Mirror guide.py's call-site behavior: lean spec → compact catalog
    # + priority tools keep full schemas. Without this the dumper
    # under-reports lean's savings vs full (both show ~17k tokens
    # because we'd be measuring the un-compressed catalog).
    is_lean = spec.prompt_mode == "lean"
    PRIORITY_TOOLS = (
        "run_python", "run_r", "Skill", "search_skills",
        "present_plan", "ask_clarification",
        "register_dataset", "list_data_files", "find_files",
        "ensure_capability", "describe_tool",
    )
    all_tools = list_tools(compact=is_lean,
                           priority_tools=(PRIORITY_TOOLS if is_lean else ()))
    tools = filter_tools_by_allowlist(all_tools, spec.tool_allowlist)
    stable, dynamic = build_system(
        active_tools=tools,
        role=spec.manifest_role or "primary",
        intent=INTENT,
        ctx={"thread_id": "thr_phase0", "project_id": "prj_phase0"},
        mode=spec.prompt_mode,
    )
    system_text = (stable + ("\n\n" + dynamic if dynamic else "")).strip()
    tools_openai = [to_openai(t) for t in tools]

    sub = OUT / spec_name
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "aba_system.txt").write_text(system_text)
    (sub / "aba_tools_anthropic.json").write_text(json.dumps(tools, indent=2))
    (sub / "aba_tools_openai.json").write_text(json.dumps(tools_openai, indent=2))

    return {
        "spec":            spec_name,
        "prompt_mode":     spec.prompt_mode,
        "system_chars":    len(system_text),
        "system_tokens":   len(system_text) // 4,
        "tools_count":     len(tools),
        "tools_chars":     len(json.dumps(tools_openai)),
        "tools_tokens":    len(json.dumps(tools_openai)) // 4,
        "static_tokens":   (len(system_text) + len(json.dumps(tools_openai))) // 4,
    }


# Convenience: also write top-level files (no subdir) that mirror the
# DEFAULT spec, so the existing smoke.py can pick them up unchanged.
def dump_default_top_level(default_spec: str) -> None:
    sub = OUT / default_spec
    for fn in ("aba_system.txt", "aba_tools_anthropic.json", "aba_tools_openai.json"):
        src = sub / fn
        if src.is_file():
            (OUT / fn).write_text(src.read_text())


reports = []
for s in ("guide", "lean_guide"):
    try:
        reports.append(dump_for_spec(s))
    except RuntimeError as e:
        print(f"[warn] skipping {s}: {e}")

dump_default_top_level("guide")

# Baseline read from real per-project DBs.
baseline: dict = {"sources": [], "samples": []}
projects_dir = Path.home() / ".aba" / "runtime" / "projects"
dbs = sorted(projects_dir.glob("*/project.db")) if projects_dir.is_dir() else []
for db in dbs:
    try:
        con  = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT json_extract(usage_blob,'$.input'),"
            "       json_extract(usage_blob,'$.output'),"
            "       json_extract(usage_blob,'$.cache_read'),"
            "       json_extract(usage_blob,'$.cache_write') "
            "FROM runs WHERE usage_blob IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT 10"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        continue
    if not rows:
        continue
    baseline["sources"].append(str(db))
    for r in rows:
        if r[0] is None and r[2] is None:
            continue
        baseline["samples"].append({"db": db.parent.name,
                                    "input": r[0],
                                    "output": r[1],
                                    "cache_read": r[2],
                                    "cache_write": r[3],
                                    "total_in": (r[0] or 0) + (r[2] or 0)
                                                + (r[3] or 0)})
baseline["samples"].sort(key=lambda s: -(s.get("total_in") or 0))
baseline["samples"] = baseline["samples"][:20]
ins = [s["total_in"] for s in baseline["samples"] if s.get("total_in")]
if ins:
    baseline["total_in_summary"] = {
        "min": min(ins), "max": max(ins),
        "median": sorted(ins)[len(ins) // 2],
        "n": len(ins),
    }
(OUT / "baseline.json").write_text(json.dumps(baseline, indent=2))


# ── Headline report ─────────────────────────────────────────────────
print()
print(f"{'spec':<14} {'mode':<6} {'tools':>6} {'sys/tok':>9} {'tools/tok':>11} {'static/tok':>12}")
print("-" * 64)
for r in reports:
    print(f"{r['spec']:<14} {r['prompt_mode']:<6} "
          f"{r['tools_count']:>6} {r['system_tokens']:>9,} "
          f"{r['tools_tokens']:>11,} {r['static_tokens']:>12,}")

if len(reports) == 2:
    full, lean = reports[0], reports[1]
    delta = full["static_tokens"] - lean["static_tokens"]
    pct = 100 * delta / max(full["static_tokens"], 1)
    print(f"\nLEAN savings vs full static: {delta:,} tokens ({pct:.1f}%)")
    # 40,960 is the Qwen3-8B vLLM window; reserve ~4k for output.
    headroom_full = 40_960 - 4_000 - full["static_tokens"]
    headroom_lean = 40_960 - 4_000 - lean["static_tokens"]
    print(f"Headroom under 40,960 (−4k output reserve):")
    print(f"  full: {headroom_full:>7,} tokens for history + tool results + user msg")
    print(f"  lean: {headroom_lean:>7,} tokens")

print()
print(f"baseline rows : {len(baseline.get('samples') or [])}"
      f" (from {len(baseline.get('sources') or [])} db(s))")
print(f"wrote → {OUT}")
