"""Cold-start data orientation (first-run / "data added").

When a scientist opens a project that has data but an empty thread, the Guide
proactively looks at the dataset and posts a short summary + a couple of
suggested next steps as the opening message — so arriving feels guided rather
than blank. The Explorer's analysis suggestion surfaces separately and quietly
(an advisor note on the dataset), never in the main dialogue.

Idempotent and cold-start only: runs once per thread, and never once the user
has started talking (guarded by message history + an `oriented` flag).
"""
from __future__ import annotations

from typing import Optional

from db import (get_messages, get_entity, update_entity, append_message,
                list_entities, WORKSPACE_ID, get_or_create_default_thread)
from proposals import _ask_json

_SYSTEM = (
    "You are the Guide, reacting to a dataset in a scientist's project. From the "
    "preview, say in 2-3 warm, plain sentences what this dataset appears to be — "
    "the modality, what rows and columns represent, and any design structure "
    "(conditions, donors, timepoints). Then propose 2-3 concrete next analyses, "
    "each a short imperative the scientist could click to run (e.g. 'Run QC on "
    "per-cell metrics'). Don't invent specifics you can't see. Output only JSON: "
    '{"summary": "...", "next_steps": ["...", "...", "..."]}'
)


def _dataset_preview(ds: dict) -> Optional[str]:
    path = ds.get("artifact_path")
    name = ds.get("title", "dataset")
    if not path:
        return f"Name: {name} (no file preview available)"
    try:
        import pandas as pd
        df = pd.read_csv(path, nrows=200)
        cols = ", ".join(f"{c}:{t}" for c, t in df.dtypes.astype(str).items())
        head = df.head(5).to_csv(index=False)
        return (f"Name: {name}\nShape (first 200 rows): {df.shape}\n"
                f"Columns: {cols}\nHead:\n{head}")
    except Exception:
        return f"Name: {name} (columns unavailable)"


def _basic_orientation(ds: dict) -> dict:
    """Deterministic, data-derived orientation — used when the model is
    unavailable (e.g. API overloaded) so the user still gets a proactive
    summary + starter chips instead of silence."""
    name = ds.get("title", "the dataset")
    try:
        import pandas as pd
        df = pd.read_csv(ds["artifact_path"], nrows=500)
        cols = list(df.columns)
        lower = {str(c).lower() for c in cols}
        shown = ", ".join(str(c) for c in cols[:8]) + ("…" if len(cols) > 8 else "")
        num = df.select_dtypes("number").columns.tolist()

        # Light heuristic guess at the data type from its columns (no model).
        sc_markers = {"n_genes", "n_counts", "mt_fraction", "cell_id", "n_genes_by_counts",
                      "total_counts", "pct_counts_mt"}
        kind = None
        if len(sc_markers & lower) >= 2:
            kind = ("looks like single-cell RNA-seq QC data — each row is a cell with "
                    "per-cell metrics")
        elif {"gene", "log2foldchange", "padj", "pvalue", "logfc"} & lower:
            kind = "looks like a differential-expression results table"
        elif {"sample", "condition", "group", "treatment"} & lower:
            kind = "looks like sample-level / experimental-design data"

        design = [c for c in ("condition", "donor", "timepoint", "sample", "group",
                              "treatment", "batch") if c in lower]
        guess = f" — {kind}" if kind else ""
        design_txt = (f", organized by {', '.join(design)}" if design else "")
        summary = (f"{name}{guess}: {len(df)}+ rows × {len(cols)} columns "
                   f"({shown}){design_txt}. (Quick read — the model was busy, so this "
                   "is a structural guess; ask me to look closer.)")

        steps = []
        if len(sc_markers & lower) >= 2:
            steps.append("Run QC on the per-cell metrics (genes, counts, mito fraction)")
        if design and num:
            steps.append(f"Compare {num[0]} across {design[0]}")
        if num:
            steps.append(f"Plot distributions of {', '.join(str(c) for c in num[:2])}")
        steps.append("Summarize the columns and their value ranges")
        # de-dup, keep order, cap at 3
        seen, uniq = set(), []
        for s in steps:
            if s not in seen:
                seen.add(s); uniq.append(s)
        return {"summary": summary, "next_steps": uniq[:3]}
    except Exception:
        return {"summary": f"Added {name}. I can help you explore it.",
                "next_steps": ["Summarize this dataset", "Plot the main columns",
                               "Check data quality"]}


def _fake_orientation(ds: dict) -> dict:
    return {
        "summary": (f"This looks like a single-cell RNA-seq dataset ({ds.get('title')}) — "
                    "rows are individual cells with per-cell QC metrics (n_genes, "
                    "n_counts, mt_fraction) annotated by condition, donor, and timepoint. "
                    "The design spans multiple donors, so batch/donor effects are worth "
                    "watching."),
        "next_steps": ["Run QC on the per-cell metrics",
                       "Compare metrics across conditions",
                       "Cluster the cells and label populations"],
    }


def orient_thread(thread_id: str, dataset_id: Optional[str] = None) -> Optional[dict]:
    """React to a dataset with a Guide opening message + suggested next steps.

    Two entry points, one behavior:
      - **cold start** (no `dataset_id`): only on an empty thread; orients the
        project's most recent not-yet-oriented dataset as the opening message.
      - **new upload** (`dataset_id` given): reacts to that specific dataset even
        mid-conversation ("you just added X — …").

    De-duped per dataset (a dataset triggers at most one reaction). Best-effort:
    never raises into the caller."""
    try:
        if not thread_id:
            return None
        if thread_id == "default":
            thread_id = get_or_create_default_thread()
        thr = get_entity(thread_id)
        if not thr or thr.get("type") != "thread":
            return None
        msgs = get_messages(WORKSPACE_ID, thread_id=thread_id)
        has_convo = any(m.get("role") == "user" for m in msgs)

        if dataset_id:
            ds = get_entity(dataset_id)
            if not ds or ds.get("type") != "dataset":
                return None
        else:
            # Cold start only: never inject into an ongoing conversation.
            if has_convo:
                return None
            unoriented = [e for e in list_entities(include_archived=False)
                          if e.get("type") == "dataset"
                          and not (e.get("metadata") or {}).get("oriented")]
            if not unoriented:
                return None   # nothing new to orient to — stay quiet
            ds = unoriented[-1]

        if (ds.get("metadata") or {}).get("oriented"):
            return None   # already reacted to this dataset

        # Claim the dataset up front — BEFORE the (possibly slow) model call — so a
        # concurrent trigger can't post a duplicate orientation while we wait.
        dmeta0 = dict(ds.get("metadata") or {})
        dmeta0["oriented"] = True
        update_entity(ds["id"], metadata=dmeta0)

        cold = not has_convo
        framing = ("[This is the first data in a new project — write an opening "
                   "orientation.]" if cold else
                   "[The scientist just added this dataset to an ongoing project — "
                   "acknowledge the new data and suggest what to do with it.]")
        prompt = framing + "\n\n" + (_dataset_preview(ds) or ds.get("title", ""))
        try:
            res = _ask_json(_SYSTEM, prompt, fake=_fake_orientation(ds))
        except Exception:
            res = None   # model unavailable (e.g. API overloaded)
        if not res or not res.get("summary"):
            res = _basic_orientation(ds)   # degrade gracefully, never go silent
        if not res or not res.get("summary"):
            return None
        summary = res["summary"].strip()
        steps = [s.strip() for s in (res.get("next_steps") or []) if s.strip()][:3]

        # The summary is the durable record; the next steps render as clickable
        # starter chips attached to this message (not repeated as prose bullets).
        append_message("assistant", [{"type": "text", "text": summary}],
                       entity_id=WORKSPACE_ID, thread_id=thread_id)

        # The dataset is already marked oriented (claimed above); store the
        # starter chips on the thread.
        tmeta = dict(thr.get("metadata") or {})
        tmeta["orient_steps"] = steps
        update_entity(thread_id, metadata=tmeta)

        # The Explorer chimes in quietly (advisor note on the dataset), not in chat.
        try:
            from advisors import explorer_suggest
            explorer_suggest(ds["id"])
        except Exception:
            pass

        return {"summary": summary, "next_steps": steps, "dataset_id": ds["id"]}
    except Exception as e:  # pragma: no cover - background best-effort
        print(f"[orientation] orient_thread failed: {type(e).__name__}: {e}")
        return None
