"""
Declarative catalog of tools and skills exposed to Guide.

This is the data the Skills screen renders. As the tool surface grows
(BioMNI modules, custom workflows), entries here grow with it. Categories
follow biology domain naming where applicable, with a few generic ones
("workspace", "data") for ABA-native plumbing.
"""
from __future__ import annotations
from tools import TOOL_SCHEMAS


def _by_name(name: str) -> dict | None:
    for t in TOOL_SCHEMAS:
        if t["name"] == name:
            return t
    return None


# Per-tool metadata layered on top of the schemas. Keep the source of
# truth for argument shapes in tools.py — this is just presentation.
_TOOL_META: dict[str, dict] = {
    "list_data_files": {
        "category": "Workspace",
        "summary": "List the CSV files visible in the project data folder.",
        "example": "What files do we have?",
    },
    "read_csv_info": {
        "category": "Workspace",
        "summary": "Peek at a CSV file — schema, dtypes, first rows.",
        "example": "Read cells.csv and tell me what's in it.",
    },
    "inspect_upload": {
        "category": "Workspace",
        "summary": (
            "Inspect an opaque upload — single file, directory, or archive. "
            "Recognizes 10x Genomics and AnnData layouts."
        ),
        "example": "Inspect the file I just uploaded.",
    },
    "get_provenance": {
        "category": "Provenance",
        "summary": "Trace what data and analyses an entity was derived from.",
        "example": "How did I get this figure?",
    },
    "get_dependents": {
        "category": "Provenance",
        "summary": "Find what would need recomputing if an entity changed.",
        "example": "If I change this QC cutoff, what else is affected?",
    },
    "run_python": {
        "category": "Sandbox",
        "summary": (
            "Run Python in a sandboxed subprocess with pandas / matplotlib / "
            "scanpy / pydeseq2 available. Plots auto-register as figures."
        ),
        "example": "Plot a histogram of mt_fraction.",
    },
}


# Larger-grain "skills" — things Guide can do that aren't single tool calls
# but pipelines documented in know-how. Surfaces these alongside tools so
# the user can see what the system actually knows how to drive.
_SKILLS = [
    {
        "name": "scrna_qc_clustering",
        "category": "Single-cell",
        "summary": (
            "Compact scanpy pipeline: load → QC → filter → normalize → HVG "
            "→ PCA → neighbors → UMAP → Leiden → marker genes."
        ),
        "example": "Run a standard QC and clustering pipeline on this 10x sample.",
        "knowhow_doc": "backend/knowhow/scrna_pipeline.md",
    },
    {
        "name": "bulk_rnaseq_de",
        "category": "Bulk RNA-seq",
        "summary": (
            "DESeq2-style differential expression between two groups via "
            "pydeseq2: load → filter → fit → contrast → volcano + MA + table."
        ),
        "example": "Run DE between treated and untreated samples.",
        "knowhow_doc": "backend/knowhow/bulk_rnaseq_de.md",
    },
    {
        "name": "create_scenario_variant",
        "category": "Analysis flow",
        "summary": (
            "Re-run a figure's producing code with parameter changes the "
            "user describes (e.g. 'cap mt_fraction at 0.10'); the variant "
            "appears alongside the baseline with a Compare toggle."
        ),
        "example": "What if we used a tighter QC cutoff?",
        "knowhow_doc": None,
    },
    {
        "name": "promote_to_result",
        "category": "Result chain",
        "summary": (
            "Capture an interpretation of a focused figure as a result "
            "entity (with the figure as evidence). The Skeptic advisor "
            "automatically reviews the interpretation."
        ),
        "example": "This S4 outlier looks like doublet contamination — promote.",
        "knowhow_doc": None,
    },
]


def registry() -> dict:
    """Return the full catalog ready for /api/tools to serialize."""
    tools_out = []
    for t in TOOL_SCHEMAS:
        meta = _TOOL_META.get(t["name"], {})
        tools_out.append({
            "kind": "tool",
            "name": t["name"],
            "category": meta.get("category", "Other"),
            "summary": meta.get("summary", t.get("description", "")[:200]),
            "example": meta.get("example"),
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema"),
        })
    skills_out = [{"kind": "skill", **s} for s in _SKILLS]
    items = tools_out + skills_out

    # Group by category, preserve a sensible order.
    order = [
        "Workspace", "Sandbox", "Provenance", "Analysis flow", "Result chain",
        "Single-cell", "Bulk RNA-seq", "Other",
    ]
    cats: dict[str, list] = {c: [] for c in order}
    for it in items:
        cats.setdefault(it["category"], []).append(it)
    return {
        "categories": [
            {"name": c, "items": cats[c]}
            for c in [*order, *(k for k in cats if k not in order)]
            if cats[c]
        ],
        "total": len(items),
    }
