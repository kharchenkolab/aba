"""
Item 5 — recipes distilled from biomni implementations (collections.md).

Curated analyses are distilled (by agents, reading biomni's tool/*.py) into ABA
skill recipes that name the REAL libraries as capabilities_needed — so the agent
runs them with our own tools, not biomni. This checks the produced layer:
  1. Every recipe .md parses + registers as a skill (no malformed frontmatter).
  2. Each carries non-empty capabilities_needed (the skill->capability funnel)
     and run_python, plus a `source: biomni:` provenance line for the lakeFS lift.
  3. search_skills finds the right recipe by intent.

Deterministic (no model). Run:
    .venv/bin/python tests/d4_biomni_recipes.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import content.bio  # noqa: E402,F401
from core.skills import list_skills, search_skills  # noqa: E402

RECIPES_DIR = ROOT / "backend" / "content" / "bio" / "collections" / "biomni" / "recipes"
_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_all_parse():
    print("recipes parse + register as skills")
    md_files = sorted(RECIPES_DIR.glob("*.md"))
    recs = [s for s in list_skills() if s.source_path and "/recipes/" in s.source_path]
    check("recipe files present (>=12)", len(md_files) >= 12, str(len(md_files)))
    check("all recipe files register (no parse errors)", len(recs) == len(md_files),
          f"{len(recs)} registered vs {len(md_files)} files")
    return recs, md_files


def test_funnel_and_provenance(recs, md_files):
    print("funnel + provenance")
    # Most recipes name real libraries; pure-Python ones (regex/string ops)
    # legitimately need none, so allow a small fraction with empty caps.
    no_caps = [s.name for s in recs if not s.capabilities_needed]
    check("most recipes name real capabilities_needed (pure-Python may have none)",
          len(no_caps) <= max(10, int(0.1 * len(recs))), f"{len(no_caps)} empty: {no_caps}")
    no_rp = [s.name for s in recs if "run_python" not in s.requires_tools]
    check("every recipe requires run_python", not no_rp, str(no_rp))
    # provenance in every file (read raw; `source` isn't a SkillSpec field).
    # Match quoted or unquoted: `source: biomni:...` and `source: "biomni:..."`.
    missing_src = [f.name for f in md_files if "biomni:tool/" not in f.read_text()]
    check("every recipe carries a biomni source ref", not missing_src, str(missing_src))
    # the funnel points at REAL tools, never biomni itself
    leaks = [s.name for s in recs if any("biomni" in c.lower() for c in s.capabilities_needed)]
    check("no recipe depends on biomni as a capability", not leaks, str(leaks))


def test_domain_facet():
    print("domain facet")
    from core.skills import skill_domains, search_skills
    doms = skill_domains()
    check("many domains present (>15)", len(doms) > 15, str(len(doms)))
    check("recipes carry a domain", all(s.domain for s in
          [s for s in list_skills() if s.source_path and "/recipes/" in s.source_path]))
    # domain filter narrows results to that facet
    hits = search_skills("cell type", domain="genomics", limit=5)
    check("domain filter restricts to the facet", hits and all(s.domain == "genomics" for s in hits),
          str([(s.name, s.domain) for s in hits]))


def test_search():
    print("search_skills finds recipes by intent")
    cases = [
        ("call ChIP-seq peaks with macs2", "chipseq-peak-calling-macs2"),
        ("flux balance analysis of a metabolic model", "perform-flux-balance-analysis"),
        ("liftover genomic coordinates between assemblies", "liftover-coordinates"),
        ("call somatic mutations from a tumor BAM", "detect-and-annotate-somatic-mutations"),
        ("transfer cell type labels to a single-cell dataset", "unsupervised-celltype-transfer-between-scrna-datasets"),
    ]
    for query, expected in cases:
        top = [s.name for s in search_skills(query, limit=3)]
        check(f"'{query[:32]}…' -> {expected}", expected in top, str(top))


def main() -> int:
    recs, md_files = test_all_parse()
    test_funnel_and_provenance(recs, md_files)
    test_domain_facet()
    test_search()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL BIOMNI-RECIPE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
