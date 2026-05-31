"""Result-as-collection member helpers.

A `result` entity is a kept grouping: metadata.members is an ordered list
of panels, each {id, kind: figure|table|value|text, ref?, text?, caption?}.
The single-cell case (one member) is the common one; results grow
deliberately. Lives in bio because it knows what a 'result' is.
"""
from __future__ import annotations
from typing import Optional

from core.graph._schema import gen_entity_id
from core.graph.entities import get_entity, update_entity


def _result_members(e: dict) -> list:
    return list(((e.get("metadata") or {}).get("members")) or [])


def _save_members(result_id: str, members: list, *, invested: bool = False) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    meta = dict(e.get("metadata") or {})
    meta["members"] = members
    if invested:
        meta["invested"] = True
    return update_entity(result_id, metadata=meta)


def add_result_member(result_id: str, *, kind: str, ref: Optional[str] = None,
                      text: Optional[str] = None, caption: str = "",
                      at: Optional[int] = None,
                      invested: bool = True) -> Optional[dict]:
    """Append (or insert at `at`) a panel. Figures/tables/values carry a `ref`
    to the cell entity; text panels carry inline `text`.

    `invested=True` by default — adding a member is a user action that
    flips the Result's "invested" flag. The initial member added at
    Result creation time passes `invested=False` so a brand-new
    auto-wrapper stays archivable on unpin."""
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    members = _result_members(e)
    member = {"id": gen_entity_id("m"), "kind": kind, "caption": caption}
    if ref:
        member["ref"] = ref
    if text is not None:
        member["text"] = text
    if at is None or at < 0 or at > len(members):
        members.append(member)
    else:
        members.insert(at, member)
    return _save_members(result_id, members, invested=invested)


def remove_result_member(result_id: str, member_id: str) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    members = [m for m in _result_members(e) if m.get("id") != member_id]
    return _save_members(result_id, members, invested=True)


def update_result_member(result_id: str, member_id: str, **fields) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    members = _result_members(e)
    for m in members:
        if m.get("id") == member_id:
            # `caption_origin` ('ai' | 'user') tracks who wrote the caption so
            # the UI can tag AI-generated text (and the background auto-interpret
            # daemon knows not to overwrite a user-edited one). Mirrors the
            # interpretation_origin field on Result.metadata.
            for k in ("caption", "text", "caption_origin"):
                if k in fields and fields[k] is not None:
                    m[k] = fields[k]
    # User caption/text edits flip `invested`; auto_interpret daemon writes
    # arrive with caption_origin='ai' → keep invested unchanged.
    user_edit = (fields.get("caption_origin") == "user"
                 or (fields.get("text") is not None and "caption_origin" not in fields))
    return _save_members(result_id, members, invested=user_edit)


def reorder_result_members(result_id: str, ordered_ids: list) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    by_id = {m.get("id"): m for m in _result_members(e)}
    members = [by_id[i] for i in ordered_ids if i in by_id]
    # keep any not mentioned (defensive) at the end, original order
    members += [m for m in _result_members(e) if m.get("id") not in set(ordered_ids)]
    return _save_members(result_id, members, invested=True)
