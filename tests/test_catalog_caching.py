"""Prompt-cache placement guard.

The invariant is BEHAVIORAL, not structural: prompt caching is prefix-based over the
order tools → system → messages, so "the tools array is byte-identical" proves nothing
if a volatile block sits in front of the messages breakpoint. What must hold is that
the whole CACHED PREFIX — every block up to and including the last `cache_control`
marker — is byte-identical across two turns that differ only in per-turn state.

The earlier structural guard here asserted only that the tools array didn't change with
the dynamic tail, and passed while the live deployment re-sent the entire conversation
as fresh input on every turn (2026-07-21 telemetry: cache_read pinned at the tools+
system size, cache_write growing 43k → 17k → 38k → 58k with the history).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.llm import (  # noqa: E402
    build_cached_blocks, place_volatile_tail, _mark_last_block_cached, _CC_MARKER_BLOCK,
)

pytestmark = pytest.mark.platform

TOOLS = [{"name": "a", "approval_policy": "auto"}, {"name": "b"}, {"name": "c"}]


def _cc(b):
    return (b.get("cache_control") or {}).get("type")


def _assemble(stable_system: str, volatile: str, tools: list, history: list):
    """Reproduce _RealStream.__aenter__'s block assembly (same order, same calls)."""
    system, tool_blocks = build_cached_blocks(stable_system, tools, cc_marker=False)
    messages = _mark_last_block_cached([dict(m) for m in history])
    messages, placed = place_volatile_tail(messages, volatile)
    if not placed and volatile:
        system = [*system, {"type": "text", "text": volatile}]
    return system, tool_blocks, messages


def _cached_prefix(system, tool_blocks, messages):
    """Every block the API hashes for the deepest cache breakpoint: the flattened
    tools → system → messages sequence truncated at the LAST cache_control block."""
    flat = [*tool_blocks, *system]
    for m in messages:
        content = m.get("content")
        flat.extend(content if isinstance(content, list) else [{"text": content}])
    last = max((i for i, b in enumerate(flat) if _cc(b)), default=-1)
    return flat[:last + 1]


def _history(n_turns: int):
    h = []
    for i in range(n_turns):
        h.append({"role": "user", "content": [{"type": "text", "text": f"ask {i}"}]})
        h.append({"role": "assistant", "content": [{"type": "text", "text": f"answer {i}"}]})
    h.append({"role": "user", "content": [{"type": "text", "text": "latest question"}]})
    return h


# ── structure ───────────────────────────────────────────────────────────────

def test_catalog_and_system_are_cached():
    sysb, toolb = build_cached_blocks("STABLE", TOOLS, cc_marker=False)
    assert sysb == [{"type": "text", "text": "STABLE",
                     "cache_control": {"type": "ephemeral"}}]
    assert _cc(toolb[-1]) == "ephemeral"                                 # whole catalog cached
    assert all(_cc(t) is None for t in toolb[:-1])
    assert all("approval_policy" not in t for t in toolb)                # internal keys stripped


def test_cc_marker_first_and_uncached():
    sysb, _ = build_cached_blocks("STABLE", TOOLS, cc_marker=True)
    assert sysb[0] == _CC_MARKER_BLOCK and _cc(sysb[0]) is None          # marker first, uncached
    assert sysb[1]["text"] == "STABLE" and _cc(sysb[1]) == "ephemeral"


def test_no_tools_safe():
    sysb, toolb = build_cached_blocks("S", [], cc_marker=False)
    assert toolb == [] and _cc(sysb[0]) == "ephemeral"


# ── the behavioral invariant ────────────────────────────────────────────────

def test_volatile_change_leaves_the_cached_prefix_identical():
    """Turn N and turn N+1 differ only in per-turn state (a new dataset in the project
    snapshot, a moved compute-env line). The cached prefix must not move by one byte —
    otherwise the whole conversation is re-sent as fresh input."""
    hist = _history(6)
    a = _assemble("STABLE-SYSTEM", "[PROJECT] datasets: 3\ncompute: 12 idle nodes", TOOLS, hist)
    b = _assemble("STABLE-SYSTEM", "[PROJECT] datasets: 4\ncompute: 5 idle nodes", TOOLS, hist)
    assert _cached_prefix(*a) == _cached_prefix(*b), \
        "volatile state moved the cached prefix → the whole history re-bills as cache_write"
    # …and it is a real prefix, not an empty one: tools + system + the history are in it
    prefix = _cached_prefix(*a)
    assert len(prefix) >= len(TOOLS) + 1 + len(hist), "cache breakpoint lost coverage"


def test_volatile_text_still_reaches_the_model():
    """Placement must not silently drop content — the tail is delivered, just last."""
    system, tools, messages = _assemble("STABLE", "VOLATILE-NOTE", TOOLS, _history(1))
    rendered = [b.get("text") for m in messages for b in m["content"]]
    assert rendered[-1] == "VOLATILE-NOTE", "tail not delivered on the last message"
    assert "VOLATILE-NOTE" not in (system[0]["text"]), "tail leaked into the cached system"


def test_tail_lands_after_the_cache_mark():
    _, _, messages = _assemble("STABLE", "TAIL", TOOLS, _history(1))
    content = messages[-1]["content"]
    assert content[-1]["text"] == "TAIL" and _cc(content[-1]) is None
    assert _cc(content[-2]) == "ephemeral", "the mark must precede the volatile tail"


def test_non_user_last_message_keeps_the_tail_in_system():
    """An assistant-terminated history can't carry a user-side tail — it falls back to
    the system array (a cache miss that turn, never a dropped block)."""
    hist = [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}]
    system, _, messages = _assemble("STABLE", "TAIL", TOOLS, hist)
    assert system[-1]["text"] == "TAIL" and _cc(system[-1]) is None
    assert all(b.get("text") != "TAIL" for m in messages for b in m["content"])


def test_empty_tail_is_a_noop():
    hist = _history(1)
    msgs, placed = place_volatile_tail([dict(m) for m in hist], "")
    assert placed and msgs == hist


# ── the guide-level placement contract ──────────────────────────────────────

def test_build_system_prompt_keeps_volatile_state_out_of_the_stable_block():
    """guide._build_system_prompt must return the pack's stable block ALONE as
    `system`; the project sidebar / focus / thread preambles belong to the tail."""
    import guide as _g

    prompts = {"system": lambda tools, role, intent, ctx, mode: ("PACK-STABLE", "RECIPES")}
    system, dynamic = _g._build_system_prompt(
        prompts, [], None, "role", "intent", {},
        sidebar_text="[PROJECT snapshot]\n", focus_text="[FOCUS]\n", thread_text="[THREAD]\n")
    assert system == "PACK-STABLE", "volatile state is back in the cached system block"
    for piece in ("[PROJECT snapshot]", "[FOCUS]", "[THREAD]", "RECIPES"):
        assert piece in dynamic, f"{piece} lost from the volatile tail"
    # relative order preserved: state, then the recipe slice
    assert dynamic.index("[PROJECT snapshot]") < dynamic.index("[FOCUS]") \
        < dynamic.index("[THREAD]") < dynamic.index("RECIPES")


def test_build_system_prompt_stable_block_is_turn_invariant():
    """Two turns with different project state → byte-identical `system`."""
    import guide as _g

    prompts = {"system": lambda tools, role, intent, ctx, mode: ("PACK-STABLE", "")}
    s1, d1 = _g._build_system_prompt(prompts, [], None, "r", "i", {},
                                     "[PROJECT] 3 datasets\n", "", "")
    s2, d2 = _g._build_system_prompt(prompts, [], None, "r", "i", {},
                                     "[PROJECT] 4 datasets\n", "[FOCUS fig_1]\n", "")
    assert s1 == s2 == "PACK-STABLE"
    assert d1 != d2                       # the change is real, it just moved to the tail


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f(); print(f"ok  {_n}")
    print("all prompt-cache placement guards passed")
