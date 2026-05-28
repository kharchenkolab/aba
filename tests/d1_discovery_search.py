"""
Discovery search (items 1+2 of the capability/skill search work):

  1. BM25 intent search beats substring for skills + capabilities.
  2. The in-prompt skills index is RETRIEVAL-GATED — its imprint stays
     bounded as the recipe library grows to 100+ (the scalability crux).
  3. Skill→capability funnel: a skill declares capabilities_needed; read_skill
     surfaces them so the agent can ensure_capability the gaps.

Deterministic (no model). Run:
    .venv/bin/python tests/d1_discovery_search.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d1_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d1.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db              # noqa: E402
import content.bio  # noqa: E402,F401  (registers real skills + seeds catalog)
from core.search import BM25                         # noqa: E402
import core.skills.loader as loader                  # noqa: E402
from core.skills import search_skills, skills_index_block, get_skill  # noqa: E402
from core.catalog import search_capabilities         # noqa: E402
from content.bio.tools import read_skill, search_skills_tool, list_capabilities_tool  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_bm25_unit():
    print("BM25 primitive")
    docs = [
        ("a", "salmon star align rna-seq reads to a genome quantify"),
        ("b", "deseq2 differential expression bulk rna-seq counts"),
        ("c", "scanpy single cell clustering umap leiden"),
    ]
    idx = BM25(docs)
    top = idx.search("align reads to the genome")
    check("ranks the relevant doc first", top and top[0][0] == "a", str(top))
    check("empty query -> no hits", idx.search("") == [])
    check("non-matching query -> no hits", idx.search("zzzz nonexistent") == [])
    # idf: a term in every doc contributes little; a rare term dominates
    multi = BM25([("x", "common rna common"), ("y", "common deseq2 common")])
    check("rare term outranks common", multi.search("deseq2")[0][0] == "y")


def test_skill_intent_search():
    print("skill intent search")
    check("DE query -> bulk-rnaseq-de",
          search_skills("differential expression between treated and control")[0].name == "bulk-rnaseq-de")
    check("single-cell query -> scrna-qc-clustering",
          search_skills("cluster single cell data and make a umap")[0].name == "scrna-qc-clustering")
    # keyword recall: 'volcano' is only in keywords, not the description
    names = [s.name for s in search_skills("volcano plot")]
    check("keyword recall (volcano)", "bulk-rnaseq-de" in names, str(names))
    # the tool wrapper returns capabilities_needed alongside
    out = search_skills_tool({"query": "differential expression"})
    top = out["skills"][0]
    check("tool returns caps_needed", "pydeseq2" in top.get("capabilities_needed", []), str(top))


def test_gating_imprint_bounded():
    """The scalability crux: with 100+ skills the in-prompt index must stay
    a bounded slice, not list everything."""
    print("retrieval-gated index imprint (100+ skills)")
    big = Path(_tmp) / "bigskills"
    big.mkdir()
    topics = [
        ("variant calling germline gatk", "vc"),
        ("atac-seq peak calling macs2", "atac"),
        ("chip-seq peak annotation", "chip"),
        ("metagenomics taxonomic profiling kraken", "meta"),
        ("deconvolve spatial transcriptomics spots into cell types", "spatial"),
    ]
    for i in range(100):
        topic, tag = topics[i % len(topics)]
        (big / f"syn_{i:03d}.md").write_text(
            f"---\nname: syn-{tag}-{i:03d}\n"
            f"description: synthetic skill {i} about {topic}\n"
            f"keywords: [{topic}]\n---\n\nbody {i}\n"
        )
    # Synthetic skills register as the 'local' (recipe) tier by default — they
    # can never inflate the always-on core tier (folder-driven visibility).
    n = loader.register_skill_dir(big)
    check("registered 100 synthetic skills", n == 100, str(n))
    skills = loader.list_skills()
    total = len(skills)
    core_n = sum(1 for s in skills if s.visibility == "always")
    cookbook_n = total - core_n
    # Two-tier imprint cap: the full core set (fixed, small) + the gated recipe
    # slice. Constant in catalog size — that's the scalability invariant.
    cap = core_n + loader.GATED_TOP_K
    check("catalog now large (>FULL_LIST_MAX)", cookbook_n > loader.FULL_LIST_MAX, str(cookbook_n))

    block = skills_index_block(query="deconvolve spatial transcriptomics")
    bullets = [ln for ln in block.splitlines() if ln.strip().startswith("- `")]
    check("imprint bounded to core+top-K", len(bullets) <= cap, f"{len(bullets)} bullets (cap {cap})")
    check("surfaces the relevant skill", any("spatial" in b for b in bullets), str(bullets[:3]))
    check("points at search_skills for the rest", "search_skills" in block and f"of {cookbook_n}" in block)

    # Imprint must NOT scale with catalog size: block stays small vs listing all.
    check("block << full listing", len(bullets) < total / 5, f"{len(bullets)} vs {total}")

    # No query, still bounded (core + stable default recipe slice + pointer).
    nq = skills_index_block(query="")
    nq_bullets = [ln for ln in nq.splitlines() if ln.strip().startswith("- `")]
    check("no-query index also bounded", len(nq_bullets) <= cap, f"{len(nq_bullets)} (cap {cap})")

    # Query with no lexical overlap → still shows a default slice (never bullet-less).
    none = skills_index_block(query="zzzqqq nonexistent gibberish")
    none_bullets = [ln for ln in none.splitlines() if ln.strip().startswith("- `")]
    check("no-overlap query falls back to a slice", 0 < len(none_bullets) <= cap,
          f"{len(none_bullets)} (cap {cap})")


def test_capability_intent_search():
    print("capability intent search")
    # multi-word intent: the seed catalog has gseapy (enrichment).
    hits = [c.get("name") for c in search_capabilities("gene set enrichment analysis")]
    check("enrichment intent -> gseapy ranked", "gseapy" in hits, str(hits))
    # substring fallback: partial word 'enrich' (BM25 whole-token would miss)
    hits2 = [c.get("name") for c in search_capabilities("enrich")]
    check("partial-word recall via substring", "gseapy" in hits2, str(hits2))
    # the tool ranks when query present
    out = list_capabilities_tool({"query": "differential expression"})
    names = [c["name"] for c in out["capabilities"]]
    check("tool ranks pydeseq2 for DE", "pydeseq2" in names, str(names))


def test_skill_capability_funnel():
    print("skill -> capability funnel")
    r = read_skill({"name": "bulk-rnaseq-de"})
    check("read_skill returns capabilities_needed",
          "pydeseq2" in r.get("capabilities_needed", []), str(r.get("capabilities_needed")))
    check("read_skill nudges ensure_capability", "ensure_capability" in (r.get("note") or ""), str(r.get("note")))
    # a skill with no caps declared -> no funnel note (don't nag)
    r2 = read_skill({"name": "summarize-thread"})
    check("no caps -> no funnel note", not r2.get("note"), str(r2.get("note")))


def main() -> int:
    init_db()
    test_bm25_unit()
    test_skill_intent_search()
    test_gating_imprint_bounded()
    test_capability_intent_search()
    test_skill_capability_funnel()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL DISCOVERY-SEARCH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
