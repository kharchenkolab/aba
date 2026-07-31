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
    build_cached_blocks, place_volatile_tail, _mark_history_cached, _CC_MARKER_BLOCK,
)

pytestmark = pytest.mark.platform

TOOLS = [{"name": "a", "approval_policy": "auto"}, {"name": "b"}, {"name": "c"}]


def _cc(b):
    return (b.get("cache_control") or {}).get("type")


def _assemble(stable_system: str, volatile: str, tools: list, history: list):
    """Reproduce _RealStream.__aenter__'s block assembly (same order, same calls)."""
    system, tool_blocks = build_cached_blocks(stable_system, tools, cc_marker=False)
    messages = _mark_history_cached([dict(m) for m in history])
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
    """Placement must not silently drop content — the tail is delivered, just last,
    wrapped in <system-reminder> so harness-injected state isn't read as user text."""
    system, tools, messages = _assemble("STABLE", "VOLATILE-NOTE", TOOLS, _history(1))
    rendered = [b.get("text") for m in messages for b in m["content"]]
    assert rendered[-1] == "<system-reminder>\nVOLATILE-NOTE\n</system-reminder>", \
        "tail not delivered (or not wrapped) on the last message"
    assert "VOLATILE-NOTE" not in (system[0]["text"]), "tail leaked into the cached system"


def test_tail_lands_after_the_cache_mark():
    _, _, messages = _assemble("STABLE", "TAIL", TOOLS, _history(1))
    content = messages[-1]["content"]
    assert "TAIL" in content[-1]["text"] and _cc(content[-1]) is None
    assert _cc(content[-2]) == "ephemeral", "the mark must precede the volatile tail"


def test_second_anchor_survives_an_oversized_turn():
    """The lookback window is 20 blocks: if one agentic turn appends more than
    that, the NEWEST mark can't find the prior cache entry. The mark on the
    previous user message re-anchors the prior request's breakpoint position, so
    the prefix up to there still reads from cache. Assert both anchors exist and
    that the prefix up to the SECOND-newest mark is byte-stable when a huge turn
    is appended."""
    hist = _history(3)
    before = _assemble("S", "tail-1", TOOLS, hist)
    # one oversized agentic turn: assistant with 15 tool_use + user with 15 results
    big_asst = {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t{i}", "name": "a", "input": {}} for i in range(15)]}
    big_user = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"} for i in range(15)]}
    after = _assemble("S", "tail-2", TOOLS, [*hist, big_asst, big_user])
    marks_after = [b for m in after[2] for b in m["content"] if _cc(b)]
    assert len(marks_after) == 2, "both user-message anchors must be marked"
    # prefix up to the OLD anchor (last mark of `before`) is unchanged in `after`
    old_prefix = _cached_prefix(*before)
    flat_after = [*after[1], *after[0]]
    for m in after[2]:
        flat_after.extend(m["content"])
    # strip marks for byte comparison of the shared span (marks may move)
    def _bare(bs): return [{k: v for k, v in b.items() if k != "cache_control"} for b in bs]
    assert _bare(flat_after[:len(old_prefix)]) == _bare(old_prefix), \
        "an oversized turn must not disturb the previously cached span"


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


# ── the pack-level contract: NO per-turn input may move the stable block ─────

def test_pack_stable_block_invariant_across_all_per_turn_inputs():
    """The REAL system builder, parametrized over every per-turn input build_system
    accepts — intent and each prompt_ctx gate flag. The stable block must be
    byte-identical across all of them: a gated block that renders into stable
    busts system + whole-history cache on the turns the gate flips (the
    focus-flip leak this guard was added for — 1.4KB of `highlighting` moved
    the stable prefix whenever a figure took focus)."""
    from core.runtime.mcp.gateway import register_inprocess_server, list_tools
    from content.bio.mcp_servers.aba_core import make_server
    from content.bio.prompts.build import build_system
    try:
        register_inprocess_server("aba_core", make_server,
                                  expose_in_catalog=True,
                                  strip_prefix_in_catalog=True)
    except Exception:  # noqa: BLE001 — already registered in this process
        pass
    tools = list_tools(mode="standard")
    base_ctx = {"thread_id": "t1", "focus_is_figure": False, "highlight_active": False}
    variants = [
        ("intent flip", "summarize the table", dict(base_ctx)),
        ("figure focus", "i", {**base_ctx, "focus_is_figure": True}),
        ("highlight", "i", {**base_ctx, "focus_is_figure": True, "highlight_active": True}),
    ]
    s_ref, _ = build_system(tools, role="primary", intent="i", ctx=base_ctx, mode="standard")
    assert s_ref, "stable block came back empty — setup error, not a verdict"
    for label, intent, ctx in variants:
        s_var, d_var = build_system(tools, role="primary", intent=intent, ctx=ctx,
                                    mode="standard")
        assert s_var == s_ref, (
            f"per-turn input ({label}) moved the STABLE block by "
            f"{abs(len(s_var) - len(s_ref))} chars — gated content must ride the "
            f"dynamic tail (set dynamic=True on the block)")
    # the gated content still reaches the model — via the tail
    _, d_fig = build_system(tools, role="primary", intent="i",
                            ctx={**base_ctx, "focus_is_figure": True}, mode="standard")
    _, d_base = build_system(tools, role="primary", intent="i", ctx=base_ctx,
                             mode="standard")
    assert len(d_fig) > len(d_base), "gated block was dropped instead of moved to the tail"


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f(); print(f"ok  {_n}")
    print("all prompt-cache placement guards passed")
