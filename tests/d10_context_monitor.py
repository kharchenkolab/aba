"""
Context monitor — assert the agent actually RECEIVES what we think it should.

The GEO sessions failed not because discovery was broken but because the
agent's *context* didn't contain the right recipe: a domain-heavy phrasing
("analyze PBMC scRNA-seq … in GSE192391") filled all 8 recipe slots with
analysis recipes and crowded out the data-acquisition recipe, so the agent
never saw it and hand-rolled scraping. This test builds the REAL system prompt
(content.bio.prompts.build.build_system) for a battery of realistic messages
and checks the expected recipes + always-on guidance are present.

Deterministic (no model). Isolated temp DB. Run:
    .venv/bin/python tests/d10_context_monitor.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["ABA_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="aba_ctx10_"), "t.db")

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
# Post-WU-1: tools come from aba_core's MCP catalog. TOOL_SCHEMAS
# is empty; the agent's active_tools list is built from mcp_list_tools().
# Register aba_core here so list_tools() actually returns the 46
# tools — without this the build_system gates that key on tool
# names (memory/scenarios/skills/...) wouldn't fire.
from core.runtime.mcp import (  # noqa: E402
    register_inprocess_server, list_tools as mcp_list_tools, _reset_for_testing,
)
from content.bio.mcp_servers.aba_core import make_server  # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server,
                          expose_in_catalog=True,
                          strip_prefix_in_catalog=True)

from content.bio.prompts.build import build_system  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def prompt_for(msg: str) -> str:
    # Realistic: the guide gets ALL tools (allowlist '*').
    # Post-WU-1 the tool catalog comes from mcp_list_tools (aba_core's
    # bare-name listing), not the now-empty TOOL_SCHEMAS.
    return build_system(mcp_list_tools(), role="primary", intent=msg)


# (message, [recipe/skill names that MUST appear in the prompt's skills slice])
CASES = [
    # The regression that started this: a mixed acquire+analyze intent must still
    # surface the acquisition recipe AND keep an analysis recipe.
    ("I'd like to analyze PBMC scRNA-seq data in GSE192391. What samples are included?",
     ["fetch-geo-processed-matrices", "scrna-qc-clustering|conos-integration|annotate-celltype"]),
    ("download the processed count matrices for GEO sample GSM5746259",
     ["fetch-geo-processed-matrices"]),
    ("get the raw FASTQ files for SRA project SRP351944",
     ["fetch-sequencing-fastq"]),
    ("QC and cluster my single-cell RNA-seq dataset",
     ["scrna-qc-clustering"]),
    ("differential expression between treated and control bulk RNA-seq",
     ["bulk-rnaseq-de"]),
    ("register these files as a dataset and pin the figure",
     ["manage-entities"]),
]


def test_prompt_contains_expected_recipes():
    print("A. realistic message -> expected recipes present in the prompt")
    for msg, expected in CASES:
        p = prompt_for(msg)
        for exp in expected:
            # 'a|b' means any-of (at least one alternative present)
            ok = any((f"`{name}`" in p) or (name in p) for name in exp.split("|"))
            check(f"'{msg[:42]}' has {exp}", ok)


def test_always_on_guidance():
    print("B. always-on guidance is in every primary prompt")
    p = prompt_for("anything")
    check("discovery-first behavior rule", "name the concrete thing you need to do" in p)
    check("don't-fire-a-look-alike-tool rule", "looks topically related" in p)
    check("anti-fabrication rule", "Never invent or infer data" in p)
    check("failed-tool rule", "A failed tool call means that step failed" in p)
    check("don't-claim-unconfirmed-outputs rule", "Don't claim outputs you haven't confirmed" in p)
    check("core skill: approach-unfamiliar-tool", "approach-unfamiliar-tool" in p)
    check("core skill: manage-entities", "manage-entities" in p)
    check("Core skills section header", "**Core skills**" in p)


def test_no_single_domain_monopoly():
    print("C. no single domain takes every recipe slot (the crowd-out guard)")
    # The failing intent: count distinct domains among the shown recipes.
    from core.skills.loader import skills_index_block
    from core.skills import get_skill
    blk = skills_index_block("I'd like to analyze PBMC scRNA-seq data in GSE192391. What samples are included?")
    after = blk.split("**Recipes**")[-1]
    names = [l.split("`")[1] for l in after.splitlines() if l.startswith("- `")]
    doms = {get_skill(n).domain for n in names if get_skill(n)}
    check("recipe slice spans >1 domain", len(doms) > 1, f"domains={doms}")
    check("includes a data/database recipe", any(get_skill(n) and get_skill(n).domain == "database" for n in names),
          str(names))


def main() -> int:
    test_prompt_contains_expected_recipes()
    test_always_on_guidance()
    test_no_single_domain_monopoly()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures)); return 1
    print("ALL CONTEXT-MONITOR CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
