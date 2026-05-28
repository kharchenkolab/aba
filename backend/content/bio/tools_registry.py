"""
Declarative catalog of tools and skills exposed to Guide.

This is the data the Skills screen renders. As the tool surface grows
(BioMNI modules, custom workflows), entries here grow with it. Categories
follow biology domain naming where applicable, with a few generic ones
("workspace", "data") for ABA-native plumbing.
"""
from __future__ import annotations
from content.bio.tools import TOOL_SCHEMAS
from core.graph.tool_settings import get_disabled_tools
from core.skills import list_skills


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


# Larger-grain "skills" are authored markdown procedures in the content
# library (content/bio/library/{skills,recipes}/), loaded into the same
# registry the agent searches via search_skills. The catalog renders them
# straight from that registry — single source of truth, no hardcoded copy.
def _skill_category(domain: str) -> str:
    """Map a skill's `domain` facet to a catalog category. Platform/workflow
    skills carry no domain — they're ABA-native plumbing, grouped as such."""
    d = (domain or "").strip().lower()
    if not d:
        return "Analysis flow"          # ABA-native workflow skills
    return _DOMAIN_LABELS.get(d, d.replace("_", " ").capitalize())


_DOMAIN_LABELS = {
    "meta": "Strategy",
    "genomics": "Genomics",
    "molecular_biology": "Molecular biology",
    "immunology": "Immunology",
    "pharmacology": "Pharmacology",
    "biochemistry": "Biochemistry",
}


# BioMNI domain modules — importable inside the sandbox (run_python adds the
# vendored repo to sys.path). Listed here so they're discoverable; some need
# extra dependencies that may not be installed yet.
_BIOMNI_MODULES = [
    ("genomics", "Variant calling, GWAS, sequence analysis helpers."),
    ("immunology", "Immune-repertoire and cytometry analysis helpers."),
    ("molecular_biology", "Cloning, primer design, sequence manipulation."),
    ("single_cell", "Single-cell helpers (annotation, integration)."),
    ("pharmacology", "Dose-response, PK/PD, compound analysis."),
    ("literature", "Literature search and retrieval helpers."),
]


def registry() -> dict:
    """Return the full catalog ready for /api/tools to serialize."""
    disabled = get_disabled_tools()
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
            "enabled": t["name"] not in disabled,
            "toggleable": True,
        })
    skills_out = [{
        "kind": "skill",
        "name": s.name,
        "category": _skill_category(s.domain),
        "summary": s.description,
        "when_to_use": s.when_to_use or None,
        "capabilities_needed": list(s.capabilities_needed) or None,
        "source": s.source or None,
        "enabled": True,
        "toggleable": False,
    } for s in list_skills()]
    biomni_out = [{
        "kind": "module", "name": f"biomni.tool.{m}", "category": "BioMNI library",
        "summary": desc, "example": f"from biomni.tool.{m} import ...  (inside run_python)",
        "enabled": True, "toggleable": False,
    } for m, desc in _BIOMNI_MODULES]
    items = tools_out + skills_out + biomni_out

    # Group by category, preserve a sensible order. Tool categories first,
    # then skill domains (any unlisted ones append after), BioMNI + Other last.
    order = [
        "Workspace", "Sandbox", "Provenance", "Analysis flow", "Strategy",
        "Genomics", "Molecular biology", "Immunology", "Pharmacology",
        "Biochemistry", "BioMNI library", "Other",
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
