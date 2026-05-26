"""Thread-context renderer.

Today's `guide._thread_context` lifted into bio. Reads the kept results
and claims for a thread and produces the prose the Guide sees as part of
its system prompt — so it knows what's already been pinned/claimed in
this line of inquiry and doesn't re-explain from raw data.
"""
from __future__ import annotations
from typing import Optional

from core.graph.entities import list_entities


def render_thread_context(thread_id: Optional[str]) -> str:
    if not thread_id:
        return ""
    pinned: list[str] = []
    claims: list[str] = []
    for e in list_entities(include_archived=False):
        m = e.get("metadata") or {}
        if m.get("thread_id") != thread_id:
            continue
        if e.get("type") in ("figure", "table") and e.get("pinned"):
            interp = (m.get("interpretation") or "").strip()
            pinned.append(f"- {e.get('title', '')}" + (f" — {interp}" if interp else ""))
        elif e.get("type") == "claim":
            claims.append(f"- {m.get('statement') or e.get('title')} ({m.get('confidence', 'preliminary')})")
    if not pinned and not claims:
        return ""
    out = ["### Kept in this thread"]
    if pinned:
        out.append("Pinned results (this thread's evidence — refer to these directly; "
                   "they may be figures/tables produced by analysis runs):")
        out += pinned[:20]
    if claims:
        out.append("Claims so far:")
        out += claims[:20]
    return "\n".join(out) + "\n\n"
