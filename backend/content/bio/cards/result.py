"""Focus card for Result entities.

A Result is a *container* of one-or-more figures / tables / notes.
The generic card (title + status + tags) tells the agent which Result
the user is looking at, but says NOTHING about which figures the
Result contains -- so when the user asks 'what figure am I looking at
now?' the agent has to guess from conversation-history recency, which
fails as soon as the user navigates between Results in the same thread
(focus regression 2026-06-07, thr_806a2ced).

This builder lists the members up front so the focus preamble carries
enough information to anchor the agent on the actual displayed content.

Member ordering matches the Result's metadata.members[] (visible order
in the panel). For figure members we also surface:
  - the displayed (latest) revision id when the chain has more than
    one entry, because the panel renders chain[0] by default and the
    member.ref is the original anchor (which is NOT what the user sees)
  - the artifact path so 'what figure are we discussing' becomes
    unambiguous when there are sibling figures with similar titles.
"""
from __future__ import annotations

from typing import Any

from core.manifest.assembler import _generic_card, register_card_builder
from core.graph.entities import get_entity


def build_result_card(entity: dict,
                      *, focus_member_id: str | None = None,
                      ) -> tuple[str, list[str]]:
    """Result focus card. When the chat request carries a
    `focus_member_id` (sent by the frontend when a multi-member Result
    is in view), the matching line is tagged with an in-view marker so
    the agent anchors on it for "this plot" gestures.

    `focus_member_id` is None when:
      - the Result has only one major member (no need to disambiguate
        — single-panel behavior is unchanged), or
      - the frontend hasn't reported a viewport pick (e.g. older client,
        retry path that re-uses the previous payload).
    """
    text, fields = _generic_card(entity)
    members = (entity.get("metadata") or {}).get("members") or []
    if not isinstance(members, list) or not members:
        # An empty Result still falls through to generic-card output;
        # add a small hint so the agent doesn't propose actions that
        # only make sense for a populated Result.
        return text + "\nThe Result has no members yet (empty placeholder).", fields

    # Only flag the in-view member when it's actually in this Result's
    # list — protects against a stale id from a prior focus.
    valid_focus = focus_member_id if any(
        isinstance(m, dict) and m.get("id") == focus_member_id
        for m in members
    ) else None

    lines: list[str] = []
    header = f"Members ({len(members)})"
    if valid_focus and len(members) > 1:
        header += "  — the user has one of these in their viewport; see ← marker below"
    lines.append(header + ":")
    for m in members:
        if not isinstance(m, dict):
            continue
        kind = m.get("kind") or "unknown"
        ref = m.get("ref")
        mid = m.get("id")
        marker = "  ← user is looking at this one" if (
            valid_focus and mid == valid_focus and len(members) > 1
        ) else ""
        if kind == "text":
            # Notes don't have a ref; render the leading text inline.
            note = (m.get("text") or "").strip()
            preview = (note[:120] + "..." if len(note) > 120 else note) or "(empty note)"
            lines.append(f"  - note: {preview!r}{marker}")
            continue
        if not ref:
            lines.append(f"  - {kind}: (unresolved ref){marker}")
            continue
        cell = get_entity(ref)
        if not cell:
            lines.append(f"  - {kind}: id={ref} (not found){marker}")
            continue
        # For figures / tables, surface the displayed revision when a
        # chain exists -- the panel shows chain[0] (latest), NOT the
        # original anchor that member.ref points to.
        displayed = cell
        chain_len = 1
        if cell.get("type") in ("figure", "table"):
            try:
                from content.bio.graph.figure_history import figure_history
                chain = figure_history(ref)
                if chain:
                    displayed = chain[0]
                    chain_len = len(chain)
            except Exception:  # noqa: BLE001 - chain walk is best-effort
                pass
        bits: list[str] = [f"{cell.get('type','?')} {(displayed.get('title') or '').strip()!r}"]
        bits.append(f"id={displayed.get('id')}")
        if displayed.get("artifact_path"):
            bits.append(f"artifact={displayed['artifact_path']}")
        if chain_len > 1 and displayed.get("id") != ref:
            bits.append(f"displayed revision (rev {chain_len}/{chain_len}), anchor={ref}")
        lines.append(f"  - " + ", ".join(bits) + marker)

    text = text + "\n" + "\n".join(lines)
    fields.append("result_members")
    if valid_focus and len(members) > 1:
        fields.append("result_focus_member")
    return text, fields


register_card_builder("result", build_result_card)
