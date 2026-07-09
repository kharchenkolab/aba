"""tool_use a2: prompt-cache structure guard. The tool catalog + stable system must
sit in cache-controlled blocks (so per-turn cost is cache_read, not fresh tokens), and
the per-turn dynamic tail must NOT bust that cache. Guards build_cached_blocks so a
future edit can't silently move the catalog out of the cached prefix."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.llm import build_cached_blocks, _CC_MARKER_BLOCK  # noqa: E402

TOOLS = [{"name": "a", "approval_policy": "auto"}, {"name": "b"}, {"name": "c"}]


def _cc(b):
    return (b.get("cache_control") or {}).get("type")


def test_catalog_and_system_are_cached():
    sysb, toolb = build_cached_blocks("STABLE", "DYN", TOOLS, cc_marker=False)
    assert sysb[0]["text"] == "STABLE" and _cc(sysb[0]) == "ephemeral"   # stable cached
    assert sysb[1]["text"] == "DYN" and _cc(sysb[1]) is None             # dynamic tail uncached
    assert _cc(toolb[-1]) == "ephemeral"                                 # whole catalog cached
    assert all(_cc(t) is None for t in toolb[:-1])
    assert all("approval_policy" not in t for t in toolb)                # internal keys stripped


def test_cc_marker_first_and_uncached():
    sysb, _ = build_cached_blocks("STABLE", "", TOOLS, cc_marker=True)
    assert sysb[0] == _CC_MARKER_BLOCK and _cc(sysb[0]) is None          # marker first, uncached
    assert sysb[1]["text"] == "STABLE" and _cc(sysb[1]) == "ephemeral"


def test_dynamic_tail_does_not_bust_catalog_cache():
    # The invariant: a per-turn change in the dynamic recipes tail must leave the
    # cached system prefix + the ENTIRE tools array byte-identical (cache stays warm).
    s1, t1 = build_cached_blocks("STABLE", "recipes-turn-1", TOOLS, cc_marker=False)
    s2, t2 = build_cached_blocks("STABLE", "totally-different-turn-2", TOOLS, cc_marker=False)
    assert s1[0] == s2[0], "stable system block changed → cache busted"
    assert t1 == t2, "tools array changed with the dynamic tail → catalog cache busted"


def test_no_tools_safe():
    sysb, toolb = build_cached_blocks("S", "", [], cc_marker=False)
    assert toolb == [] and _cc(sysb[0]) == "ephemeral"


if __name__ == "__main__":
    test_catalog_and_system_are_cached(); print("ok  catalog + stable system cached")
    test_cc_marker_first_and_uncached(); print("ok  CC marker first + uncached")
    test_dynamic_tail_does_not_bust_catalog_cache(); print("ok  dynamic tail doesn't bust cache")
    test_no_tools_safe(); print("ok  no-tools safe")
    print("all catalog-caching guard tests passed")
