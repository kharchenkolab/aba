"""Mechanical README generator for the files-tree containers.

Per-container-type templated prose, computed from entity metadata +
children. No LLM in this version; that comes later via the filer
sub-agent.

Output is always Markdown. The tree builder embeds the rendered content
in the README node; the materializer writes it to disk and sets the
file's mtime to the entity's created_at.
"""
from __future__ import annotations
from typing import Iterable, Optional


def render_readme(container_kind: str, *,
                  entity: Optional[dict] = None,
                  children: Optional[list[dict]] = None,
                  members: Optional[list[dict]] = None,
                  root_entities: Optional[list[dict]] = None) -> str:
    if container_kind == "project":
        return _project_readme(root_entities or [])
    if container_kind == "thread":
        return _thread_readme(entity or {})
    if container_kind == "run":
        return _run_readme(entity or {}, children or [])
    if container_kind == "result":
        return _result_readme(entity or {}, members or [])
    if container_kind == "finding":
        return _finding_readme(entity or {})
    return _generic_readme(container_kind, entity or {})


# ---------- per-kind generators ----------

def _project_readme(entities: list[dict]) -> str:
    threads = [e for e in entities if e["type"] == "thread"]
    datasets = [e for e in entities if e["type"] == "dataset"]
    findings = [e for e in entities if e["type"] == "finding"]
    figures = [e for e in entities if e["type"] == "figure"]
    tables = [e for e in entities if e["type"] == "table"]
    lines = [
        "# Project",
        "",
        "Workspace folder mirrored from the entity graph.",
        "",
        "## Contents",
        f"- {len(threads)} thread(s)" if threads else "",
        f"- {len(datasets)} dataset(s)" if datasets else "",
        f"- {len(findings)} promoted finding(s)" if findings else "",
        f"- {len(figures)} figure(s)" if figures else "",
        f"- {len(tables)} table(s)" if tables else "",
        "",
        "## Layout",
        "- `datasets/` — project inputs",
        "- `threads/`  — work, organized by line of inquiry",
        "- `findings/` — promoted, cross-thread bundles",
        "- `orphans/`  — outputs not yet attached to a thread",
        "",
        "See `conventions.md` for the naming and layout rules.",
    ]
    return "\n".join(l for l in lines if l is not None) + "\n"


def _thread_readme(thread: dict) -> str:
    meta = thread.get("metadata") or {}
    q = (meta.get("question") or "").strip()
    lifecycle = meta.get("lifecycle") or "open"
    open_qs = meta.get("open_questions") or []
    lines = [f"# Thread: {thread.get('title') or 'Untitled'}", ""]
    if q:
        lines.append(f"> {q}")
        lines.append("")
    lines.append(f"**Lifecycle:** {lifecycle}")
    lines.append(f"**Created:** {(thread.get('created_at') or '')[:10]}")
    lines.append("")
    if open_qs:
        lines.append("## Open questions")
        for oq in open_qs:
            txt = oq.get("text") if isinstance(oq, dict) else str(oq)
            status = (oq.get("status") if isinstance(oq, dict) else "") or "open"
            mark = "—" if status == "open" else ("✓" if status == "answered" else "·")
            lines.append(f"- {mark} {txt}")
            if isinstance(oq, dict) and oq.get("answer"):
                lines.append(f"    > {oq['answer']}")
        lines.append("")
    lines.append("## Contents")
    lines.append("- `runs/`    — analyses run in this thread (if any)")
    lines.append("- `results/` — kept observations from this thread (if any)")
    lines.append("- `claims/`  — assertions made within this thread (if any)")
    lines.append("")
    lines.append(f"<!-- entity {thread.get('id')} -->")
    return "\n".join(lines) + "\n"


def _run_readme(run: dict, children: list[dict]) -> str:
    meta = (run.get("metadata") or {})
    run_info = meta.get("run") or {}
    status = run_info.get("status") or run.get("status") or "unknown"
    executor = run_info.get("executor")
    command = (run_info.get("command") or "").strip()

    lines = [f"# Run: {run.get('title') or 'Untitled'}", ""]
    lines.append(f"**Status:** {status}")
    lines.append(f"**Created:** {(run.get('created_at') or '')[:10]}")
    if executor:
        where = run_info.get("where")
        lines.append(f"**Executor:** {executor}" + (f" ({where})" if where else ""))
    lines.append("")
    if command:
        cmd = command if len(command) <= 300 else command[:300] + " …"
        lines.append("## Command")
        lines.append("```")
        lines.append(cmd)
        lines.append("```")
        lines.append("")

    if children:
        figs = [c for c in children if c["type"] == "figure"]
        tbls = [c for c in children if c["type"] == "table"]
        if figs:
            lines.append(f"## Figures ({len(figs)})")
            for f in figs:
                lines.append(f"- `figures/{_leaf_name(f)}` — {f.get('title') or ''}")
            lines.append("")
        if tbls:
            lines.append(f"## Tables ({len(tbls)})")
            for t in tbls:
                lines.append(f"- `tables/{_leaf_name(t)}` — {t.get('title') or ''}")
            lines.append("")
    else:
        lines.append("This run hasn't produced registered artifacts yet.")
        lines.append("")

    # Post-cutover: Run has captured code iff at least one of its exec
    # records carries code. aggregated_code_for_run returns "" if none do.
    from core.graph.exec_records import aggregated_code_for_run as _agg_code
    if _agg_code(run.get("id") or ""):
        lines.append("`producing_code.py` carries the code that produced these outputs.")
        lines.append("")

    lines.append(f"<!-- entity {run.get('id')} -->")
    return "\n".join(lines) + "\n"


def _result_readme(result: dict, members: list[dict]) -> str:
    meta = result.get("metadata") or {}
    interp = (meta.get("interpretation") or "").strip()
    lines = [f"# Result: {result.get('title') or 'Untitled'}", ""]
    if interp:
        lines.append(interp)
        lines.append("")
    lines.append(f"**Created:** {(result.get('created_at') or '')[:10]}")
    if result.get("pinned"):
        lines.append("**Pinned**")
    lines.append("")
    if members:
        lines.append(f"## Members ({len(members)})")
        for m in members:
            lines.append(f"- `{_leaf_name(m)}` — {m.get('title') or m.get('type')}")
        lines.append("")
    else:
        lines.append("This result has no panels yet.")
        lines.append("")
    lines.append(f"<!-- entity {result.get('id')} -->")
    return "\n".join(lines) + "\n"


def _finding_readme(finding: dict) -> str:
    meta = finding.get("metadata") or {}
    lines = [f"# Finding: {finding.get('title') or 'Untitled'}", ""]
    for key in ("statement", "abstract", "text"):
        if meta.get(key):
            lines.append(str(meta[key]))
            lines.append("")
            break
    lines.append(f"**Created:** {(finding.get('created_at') or '')[:10]}")
    confidence = meta.get("confidence")
    if confidence:
        lines.append(f"**Confidence:** {confidence}")
    lines.append("")
    lines.append(f"<!-- entity {finding.get('id')} -->")
    return "\n".join(lines) + "\n"


def _generic_readme(kind: str, entity: dict) -> str:
    return (
        f"# {kind.capitalize()}: {entity.get('title') or 'Untitled'}\n\n"
        f"_entity {entity.get('id')} · created {(entity.get('created_at') or '')[:10]}_\n"
    )


def _leaf_name(e: dict) -> str:
    """Mirror of core.files.tree._leaf_name without importing it (keeps
    the readme generator self-contained)."""
    from core.files.registry import slugify, ext_from_artifact
    slug = slugify(e.get("title") or e.get("id") or "untitled")
    t = e.get("type") or ""
    if t in ("note", "narrative", "claim"):
        return f"{slug}.md"
    ext = ext_from_artifact(e, default=".bin")
    return f"{slug}{ext}"
