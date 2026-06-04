"""Bio's project sidebar renderer — the "[PROJECT — current snapshot]"
block injected into the system prompt each turn.

Decides which entity types to surface (datasets + threads + curation
counts) and how to format them. Bio-specific by design: the SHAPE of
the snapshot is what an agent reasoning about a bio project needs to
know. Other content packs would write their own renderer.

Phase 4.3 Pass 1 of misc/phase4_entity_types.md — moved here from
core/manifest/assembler.py where it lived as a function with the four
hardcoded `type_filter="dataset"/"thread"/"result"/"claim"/"finding"`
strings (the bio-shaped vocabulary in platform code that the seam
check flagged).
"""
from __future__ import annotations
from typing import Optional

from core.graph.entities import list_entities, count_entities


def render_bio_project_sidebar(thread_id: Optional[str] = None) -> str:
    """A compact, always-fresh snapshot of project-wide entities the
    agent might want to reference across threads. Empty string when
    there's nothing to surface (so a fresh project doesn't get a
    confusing "PROJECT — (nothing)" block every turn).

    Per history-compaction redesign §4.3: shared cross-thread state
    belongs HERE — queryable, deterministic, no LLM. The thread's own
    chat history stays as the conversational record.
    """
    parts: list[str] = ["[PROJECT — current snapshot]"]

    # Datasets: small N, very useful. Show name + path so the agent
    # can pass it to inspect_upload / read straight away.
    datasets = list_entities(type_filter="dataset", include_archived=False)
    if datasets:
        parts.append(f"Datasets ({len(datasets)}):")
        for e in datasets[:10]:
            title = (e.get("title") or "").strip() or e.get("id", "")
            path = e.get("artifact_path") or ""
            line = f"  - {title}"
            if path:
                line += f"  →  {path}"
            parts.append(line)
        if len(datasets) > 10:
            parts.append(f"  (… {len(datasets) - 10} more — list_data_files for full list)")

    # Threads: small N usually. Mark the CURRENT one. Title-only —
    # detail belongs on a focused-thread card, not the firehose.
    threads = list_entities(type_filter="thread", include_archived=False)
    if threads:
        parts.append(f"Threads ({len(threads)}):")
        for t in threads[:12]:
            tid = t.get("id", "")
            title = (t.get("title") or "").strip()
            marker = " (this thread)" if thread_id and tid == thread_id else ""
            parts.append(f"  - {tid}{marker} — {title!r}")
        if len(threads) > 12:
            parts.append(f"  (… {len(threads) - 12} more)")

    # Curation counts — Results / Claims / Findings are the user's
    # judgments. Cheap one-liner; the agent can look them up by name.
    n_results = count_entities(type_filter="result", include_archived=False)
    n_claims = count_entities(type_filter="claim", include_archived=False)
    n_findings = count_entities(type_filter="finding", include_archived=False)
    if n_results or n_claims or n_findings:
        parts.append(
            f"Curated entities: results={n_results}  claims={n_claims}  findings={n_findings}"
        )

    parts.append("[/PROJECT]")
    # Collected nothing → don't emit a useless wrapper. The agent
    # shouldn't be told about an empty project state every turn.
    if len(parts) <= 2:
        return ""
    return "\n".join(parts) + "\n"
