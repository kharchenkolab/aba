"""UI-honest renderer for the simulated scientist (eval Stage 3, Layer A).

Renders the platform state as *compact text the UI surfaces* — and nothing
more. This is the validity keystone: if the renderer hides what the UI hides
(section caps, chat tail, no raw provenance graph), the agent gets lost exactly
where a human would. It calls only READ endpoints.

Used by the driver each step; also runnable as a CLI to eyeball honesty:

    python eval/driver/view.py --api http://127.0.0.1:8000/api [--focus <id>]
"""
from __future__ import annotations
import argparse
import json
import urllib.request

# Mirror the rail's per-section cap (ProjectTree SECTION_CAP) and chat tail.
SECTION_CAP = 8
CHAT_TAIL = 6
PREVIEW_ROWS = 10          # the dataset preview page size the UI shows

# Tree section order, mirroring the rail.
SECTIONS = [
    ("dataset", "Data"), ("analysis", "Analyses"), ("figure", "Figures"),
    ("table", "Tables"), ("result", "Results"), ("finding", "Findings"),
    ("claim", "Claims"), ("narrative", "Manuscript"), ("note", "Notes"),
]


def _get(api: str, path: str):
    with urllib.request.urlopen(f"{api}{path}", timeout=30) as r:
        return json.loads(r.read())


def _tree(entities: list[dict]) -> str:
    active = [e for e in entities if e.get("status") != "archived"
              and e["type"] != "workspace" and not e.get("deleted_at")]
    pinned = [e for e in active if e.get("pinned")]
    lines: list[str] = []
    if pinned:
        lines.append(f"PINNED ({len(pinned)}):")
        for e in pinned[:SECTION_CAP]:
            lines.append(f"  📌 [{e['id']}] {e['type']}: {e['title']}")
    for etype, label in SECTIONS:
        items = [e for e in active if e["type"] == etype]
        if not items:
            continue
        lines.append(f"{label} ({len(items)}):")
        for e in items[:SECTION_CAP]:
            mark = "📌 " if e.get("pinned") else ""
            stat = "" if e.get("status") == "active" else f" [{e.get('status')}]"
            lines.append(f"  {mark}[{e['id']}] {e['title']}{stat}")
        if len(items) > SECTION_CAP:
            lines.append(f"  … +{len(items) - SECTION_CAP} more (search to find them)")
    return "\n".join(lines) if lines else "(no artifacts yet)"


def _msg_text(m: dict) -> str:
    role = m["role"]
    parts = []
    for b in m.get("content", []):
        t = b.get("type")
        if t == "text":
            parts.append(b["text"])
        elif t == "tool_use":
            parts.append(f"· ran {b.get('name')}")
        # tool_result blocks are the plumbing the UI collapses — skip.
    body = "\n".join(p for p in parts if p).strip()
    if len(body) > 800:
        body = body[:800] + " …"
    return f"[{'you' if role == 'user' else 'Guide'}] {body}" if body else ""


def _chat(messages: list[dict]) -> str:
    rendered = [t for t in (_msg_text(m) for m in messages) if t]
    tail = rendered[-CHAT_TAIL:]
    head = f"(… {len(rendered) - len(tail)} earlier turns)\n" if len(rendered) > len(tail) else ""
    return head + "\n".join(tail) if tail else "(no conversation yet)"


def _focus(api: str, entities: list[dict], focus_id: str) -> str:
    ent = next((e for e in entities if e["id"] == focus_id), None)
    if not ent or ent["type"] == "workspace":
        return "FOCUS: workspace (whole project)"
    out = [f"FOCUS: {ent['type']} [{ent['id']}] — {ent['title']}"]
    meta = ent.get("metadata") or {}
    for k in ("summary", "interpretation", "maturity"):
        if meta.get(k):
            out.append(f"  {k}: {meta[k]}")
    if ent["type"] in ("dataset", "table"):
        try:
            pv = _get(api, f"/entities/{focus_id}/preview")
            if pv.get("kind") == "table":
                cols = pv.get("columns", [])
                out.append("  preview: " + ", ".join(cols))
                for row in pv.get("rows", [])[:PREVIEW_ROWS]:
                    out.append("    " + " | ".join(str(c) for c in row))
                out.append(f"  (showing up to {PREVIEW_ROWS} rows)")
        except Exception:
            pass
    if ent["type"] == "figure":
        out.append("  (a plotted figure — ask Guide about it; you can't see pixels here)")
    try:
        notes = _get(api, f"/entities/{focus_id}/advisor-notes")
        for n in notes[:3]:
            out.append(f"  advisor: {n.get('text', '')[:160]}")
    except Exception:
        pass
    return "\n".join(out)


def render(api: str, focus_id: str = "workspace", search_results: dict | None = None) -> str:
    entities = _get(api, "/entities")
    messages = _get(api, "/messages")
    blocks = [
        "=== PROJECT TREE ===\n" + _tree(entities),
        "=== " + _focus(api, entities, focus_id),
        "=== CONVERSATION ===\n" + _chat(messages),
    ]
    if search_results is not None:
        ents = search_results.get("entities", [])
        msgs = search_results.get("messages", [])
        sr = ["=== SEARCH RESULTS ==="]
        for e in ents[:SECTION_CAP]:
            sr.append(f"  [{e['id']}] {e.get('type')}: {e.get('title')}")
        for m in msgs[:5]:
            sr.append(f"  chat: …{m.get('snippet', '')[:120]}…")
        if not ents and not msgs:
            sr.append("  (no matches)")
        blocks.append("\n".join(sr))
    return "\n\n".join(blocks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000/api")
    ap.add_argument("--focus", default="workspace")
    args = ap.parse_args()
    print(render(args.api, args.focus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
