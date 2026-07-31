"""Vision payloads as REFERENCES in history, materialized on egress.

The source fix for the oversized-history class: a base64 vision block is
consumed by the model once, but persisted verbatim it burdens every request,
thread load, export, and the summarizer's input (a live incident held a ~1.3MB
image in history for ~11 generations). So durable history stores a small
`image_ref` block — `{"type": "image_ref", "tool", "path"|"entity_id",
"media_type"}` — and this module inflates the most RECENT K refs into real
image blocks at prompt-assembly time (in guide, BEFORE the [llm-prep] hash and
before any runtime, so all three lanes and the wire tripwire see one shape).

Stability: the K recent refs re-materialize to the SAME bytes each generation
(content on disk; artifact copies are content-addressed), so the prefix is
byte-stable; a ref aging past K flips to a text stub once, near the tail —
same cheap epoch profile as Tier-1's k_image_keep demotion, which remains the
cover for LEGACY inline base64 rows and user-uploaded attachments (never
ref-swapped: uploads have different retention expectations).
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.config import HISTORY_K_IMAGE_KEEP

_log = logging.getLogger(__name__)


def _as_disk(p) -> str | None:
    """A ref's stored location → a readable disk path, or None.

    Entity-backed producers store an `/artifacts/<pid>/<name>` URL here, not a
    disk path — the production-COMMON shape. It must route through the canonical
    URL→path mapping; testing `is_file()` on the URL string fails, and falling
    back to the entity record retrieves the SAME URL, so without this the whole
    entity lane silently kept full base64 in durable history."""
    if not p:
        return None
    s = str(p)
    if s.startswith("/artifacts/"):
        try:
            from core.web.artifacts import _artifact_url_to_path
            fp = _artifact_url_to_path(s)
        except Exception:  # noqa: BLE001
            return None
        return str(fp) if fp is not None and fp.is_file() else None
    return s if Path(s).is_file() else None


def _resolve_ref_path(ref: dict) -> str | None:
    """Absolute path of the referenced image, or None (deleted/unresolvable)."""
    p = _as_disk(ref.get("path"))
    if p:
        return p
    eid = ref.get("entity_id")
    if eid:
        try:
            from core.graph.entities import get_entity
            return _as_disk((get_entity(eid) or {}).get("artifact_path"))
        except Exception:  # noqa: BLE001 — resolution is best-effort
            pass
    return None


def _stub(ref: dict) -> dict:
    tool = ref.get("tool") or "view_file"
    key = "path" if ref.get("path") else "entity_id"
    val = ref.get(key) or "?"
    return {"type": "text",
            "text": f"[image demoted from context — re-view via "
                    f"{tool}({key}={str(val)[:120]!r})]"}


def materialize_image_refs(messages: list[dict],
                           k: int | None = None) -> list[dict]:
    """Inflate the most recent `k` image_ref blocks into real vision blocks;
    older (or unresolvable) refs become text stubs naming the re-view route.
    Pure with respect to the input (returns fresh structures on change); a
    history with no refs is returned untouched (fast path, no copies)."""
    k = HISTORY_K_IMAGE_KEEP if k is None else k
    ref_sites: list[tuple[int, int, int]] = []   # (msg_idx, block_idx, inner_idx)
    for i, m in enumerate(messages):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for j, b in enumerate(c):
            if isinstance(b, dict) and b.get("type") == "tool_result" \
                    and isinstance(b.get("content"), list):
                for x, inner in enumerate(b["content"]):
                    if isinstance(inner, dict) and inner.get("type") == "image_ref":
                        ref_sites.append((i, j, x))
    if not ref_sites:
        return messages
    recent = set(ref_sites[-k:]) if k > 0 else set()
    out = list(messages)
    for (i, j, x) in ref_sites:
        ref = out[i]["content"][j]["content"][x]
        block = None
        if (i, j, x) in recent:
            path = _resolve_ref_path(ref)
            if path:
                try:
                    from core.runtime.attachments import _image_vision_block
                    block = _image_vision_block(path)
                except Exception as e:  # noqa: BLE001
                    _log.warning("image_ref materialize failed for %s: %s",
                                 path, e)
        if block is None:
            block = _stub(ref)          # aged out, deleted, or decode failure
        m = dict(out[i])
        content = list(m["content"])
        tr = dict(content[j])
        inner_list = list(tr["content"])
        inner_list[x] = block
        tr["content"] = inner_list
        content[j] = tr
        m["content"] = content
        out[i] = m
    return out


def pack_tool_result_content(envelope: dict) -> list | None:
    """History-side content for a vision-envelope tool result: the text
    preamble plus an `image_ref` block when the producer supplied a
    `_vision_ref` — the payload itself never enters durable history. Returns
    None when the envelope carries no vision blocks (caller uses its normal
    JSON path); falls back to the legacy inline blocks when no ref is present
    (Tier-1's k_image_keep demotion covers those)."""
    blocks = envelope.get("_vision_blocks")
    if not isinstance(blocks, list):
        return None
    ref = envelope.get("_vision_ref")
    if not isinstance(ref, dict):
        return blocks                    # legacy producer — inline payload
    if _resolve_ref_path(ref) is None:
        # Correctness over cost: a ref unresolvable at MINT time would hand
        # the model a stub for an image it just asked to see. Keep the inline
        # payload (Tier-1's k_image_keep ages it out).
        return blocks
    out: list = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "image":
            src = b.get("source") or {}
            out.append({"type": "image_ref", **ref,
                        "media_type": src.get("media_type")
                        or ref.get("media_type") or "image/png"})
        else:
            out.append(b)
    return out
