"""Bio's project sidebar renderer — the "[PROJECT STATE]" ambient block
injected into the per-turn volatile context.

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
    # Header carries no "snapshot"/notification framing — as an appended tail
    # block it otherwise reads as a fresh message the model acknowledges every
    # turn ("Acknowledged the snapshot"); the behavior rule + this neutral label
    # keep it ambient. See behavior.md "<system-reminder> blocks are ambient".
    parts: list[str] = ["[PROJECT STATE]"]

    # Datasets: small N, very useful. Show name + path + the layout
    # hint that register_dataset computed (e.g. "6 flat files (.mtx.gz,
    # .tsv.gz)"). The path alone made the agent confidently invent
    # filenames — prj_ab1b55fe thr_e692a202 (2026-06-11) burned three
    # round-trips per workflow guessing names like
    # "GSM5746259_matrix.mtx.gz" when the real name was
    # "GSM5746259_MGI0369_1_SLAB-145-0.matrix.mtx.gz". The hint is
    # already on metadata.layout_hint (curation.py:347); just surface it.
    # The one-line nudge below sits AT THE BLOCK so the rule about
    # cwd-shifted relative paths is read at the same eye-level as the
    # tempting absolute path, not buried 10K characters later in the
    # Paths paragraph.
    datasets = list_entities(type_filter="dataset", include_archived=False)
    if datasets:
        parts.append(f"Datasets ({len(datasets)}):")
        for e in datasets[:10]:
            title = (e.get("title") or "").strip() or e.get("id", "")
            path = e.get("artifact_path") or ""
            layout = ((e.get("metadata") or {}).get("layout_hint") or "").strip()
            line = f"  - {title}"
            if path:
                line += f"  →  {path}"
            if layout:
                line += f"  ·  {layout}"
            parts.append(line)
        if len(datasets) > 10:
            parts.append(f"  (… {len(datasets) - 10} more — list_data_files for full list)")
        # One-line reminder co-located with the path. The Paths paragraph
        # in the system prompt says the same thing more verbosely; this
        # is the salient copy at the spot where it matters.
        parts.append(
            "  Tip: for exact filenames call `list_data_files()`; relative "
            "paths like `./geo_data/` resolve from the kernel's current cwd, "
            "which shifts when a Run opens — use the absolute path above or "
            "the result of list_data_files()."
        )

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

    # Curation counts — the user's judgments. Registry-driven (P3.3): any type
    # declaring `capabilities.sidebar: count` appears here, so adding a curation
    # type to the cross-thread snapshot is local. Cheap one-liners.
    from core.entity_types import registry
    _counts = [(t, count_entities(type_filter=t, include_archived=False))
               for t in sorted(registry.types_with("sidebar", "count"))]
    if any(n for _, n in _counts):
        parts.append("Curated entities: " + "  ".join(f"{t}s={n}" for t, n in _counts))

    parts.append("[/PROJECT STATE]")
    # Collected nothing → don't emit a useless wrapper. The agent
    # shouldn't be told about an empty project state every turn.
    if len(parts) <= 2:
        return ""
    return "\n".join(parts) + "\n"
