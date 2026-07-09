"""Ctx-aware READ-side bio tool impls (WU-3-tail).

Mix: `skill_tool` / `read_skill` USE ctx (for active_tools enforcement
and recipe-uptake tracking); `list_entities_tool` takes ctx but doesn't
read it; the rest are pure read-only (no ctx in signature). Grouped
here because they were migrated to aba_core together in Phase 6.C and
share the recipe-uptake tracking dict `_THREAD_READ_SKILLS` (re-exported
to __init__.py so the recipe-uptake hook also reads it)."""

from __future__ import annotations
import json
import re
from typing import Optional

# Per-thread record of which skills were `Skill`-loaded — read by the
# recipe-uptake hook (in __init__.py) so the agent isn't re-nudged to
# read a skill it already read this turn. Persisted only in-process.
_THREAD_READ_SKILLS: dict = {}


def read_csv_info(input_: dict) -> dict:
    import pandas as pd
    filename = input_.get("filename", "")
    from core.config import project_data_dir
    from core.projects import current_project_id
    path = project_data_dir(current_project_id()) / filename
    if not path.exists():
        return {"error": f"File not found: {filename}"}
    try:
        # Sniff the delimiter so a TSV isn't read as a single comma-column.
        # sep=None + the python engine uses csv.Sniffer; fall back to comma.
        try:
            df = pd.read_csv(path, sep=None, engine="python")
        except Exception:
            df = pd.read_csv(path)
        cols = [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
        # to_markdown() needs the optional `tabulate` package; degrade to a
        # plain-text preview rather than hard-failing if it's ever missing.
        try:
            preview = df.head(5).to_markdown(index=False)
        except Exception:
            preview = df.head(5).to_string(index=False)
        return {
            "filename": filename,
            "rows": len(df),
            "columns": len(df.columns),
            "column_info": cols,
            "preview": preview
        }
    except Exception as e:
        return {"error": str(e)}


def get_provenance(input_: dict) -> dict:
    from core.graph.provenance import provenance_text, neighborhood
    eid = input_.get("entity_id", "")
    # max_depth default raised to 8 here (vs. the underlying default of 3)
    # so the agent sees long revision/derivation chains in a single call.
    # A 2026-06-11 live-session bug had the agent unable to count past v3
    # in a 7-revision chain because depth-3 silently truncated the trace.
    depth = int(input_.get("max_depth") or 8)
    return {"text": provenance_text(eid, max_depth=depth),
            "graph": neighborhood(eid, max_depth=depth)["upstream"]}


def get_dependents(input_: dict) -> dict:
    from core.graph.provenance import dependents_text, neighborhood
    eid = input_.get("entity_id", "")
    depth = int(input_.get("max_depth") or 8)
    return {"text": dependents_text(eid, max_depth=depth),
            "graph": neighborhood(eid, max_depth=depth)["downstream"]}


def _invoke_skill_core(name: str, args: str, ctx: dict | None,
                       *, tool_label: str) -> dict:
    """Shared orchestration for the canonical `Skill` tool and the legacy
    `read_skill` alias. Performs recipe-uptake tracking, requires_tools check,
    capabilities-needed note, and $ARGUMENTS substitution. tool_label is the
    name used in error messages so the agent sees the same tool it called.
    """
    from core.skills import invoke_skill as _invoke
    name = (name or "").strip()
    if not name:
        return {"status": "error", "note": f"{tool_label} needs a non-empty skill name."}
    inv = _invoke(name, args or "")
    if inv is None:
        from core.skills import list_skills
        avail = [s.name for s in list_skills()]
        return {
            "status": "unknown_skill",
            "note": f"No skill named {name!r}. Available: {', '.join(avail) or '(none)'}.",
        }
    spec = inv["spec"]

    # Record that this recipe was read this turn, so the run_python/run_r
    # recipe-uptake nudge doesn't remind the agent to read what it already read.
    rc = ctx.get("recipe_ctx") if isinstance(ctx, dict) else None
    if isinstance(rc, dict):
        rc.setdefault("read", set()).add(spec.name)
    _tid = str((ctx or {}).get("thread_id") or "")
    if _tid:
        _THREAD_READ_SKILLS.setdefault(_tid, set()).add(spec.name)

    # Surface missing required tools BEFORE returning the body.
    missing: list[str] = []
    if ctx and spec.requires_tools:
        active = {t.get("name") for t in (ctx.get("active_tools") or [])}
        missing = [t for t in spec.requires_tools if t not in active]
    if missing:
        return {
            "status": "tools_unavailable",
            "skill": spec.name,
            "missing": missing,
            "note": (
                f"Skill {spec.name!r} requires tools {missing!r} which aren't active "
                f"this turn. Either pick a different approach or ask the user to "
                f"enable the missing tools."
            ),
        }

    body = inv["body"]
    if inv["resources"]:
        body = (body.rstrip()
                + "\n\n--- Bundled resources (use read_file to load on demand) ---\n"
                + "\n".join(inv["resources"]))

    out = {
        "status": "ok",
        "name": spec.name,
        "description": spec.description,
        "when_to_use": spec.when_to_use,
        "requires_tools": list(spec.requires_tools),
        "capabilities_needed": list(spec.capabilities_needed),
        "produces": list(spec.produces),
        "resources": list(inv["resources"]),
        "body": body,
    }
    if spec.capabilities_needed:
        out["note"] = (
            "This skill uses these capabilities: "
            f"{', '.join(spec.capabilities_needed)}. "
            "Call ensure_capability(name) for any not already available before run_python."
        )
    return out


def skill_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Canonical Skill envelope (CC-convergence Phase 1). Loads the skill's
    SKILL.md body with `$ARGUMENTS` substituted from `args`, and includes a
    list of bundled resources the agent can read_file on demand."""
    if not isinstance(input_, dict):
        input_ = {}
    name = input_.get("skill") or input_.get("name") or ""
    args = input_.get("args") or ""
    return _invoke_skill_core(name, args, ctx, tool_label="Skill")


def read_skill(input_: dict, ctx: dict | None = None) -> dict:
    """Deprecated alias for `Skill` (no $ARGUMENTS substitution since the
    legacy tool has no `args` field). Kept for one release of CC-convergence
    Phase 1 so a model that falls back to the old name still works."""
    if not isinstance(input_, dict):
        input_ = {}
    name = input_.get("name") or input_.get("skill") or ""
    return _invoke_skill_core(name, "", ctx, tool_label="read_skill")


def read_capability(input_: dict) -> dict:
    """Full detail for one capability by name — what it does, its inputs, and
    (for a reference entry) where the upstream implementation lives. Mirrors
    read_skill: list/search stay trimmed; this expands one on demand."""
    name = (input_.get("name") or input_.get("capability") or "").strip()
    if not name:
        return {"status": "error", "note": "read_capability needs a non-empty `name`."}
    from core.catalog import resolve_capability
    cap = resolve_capability(name)
    if not cap:
        return {"status": "not_found",
                "note": f"No capability '{name}'. Use search_capabilities to search."}
    out = {
        "status": "ok",
        "name": cap.get("name"),
        "archetype": cap.get("archetype"),
        "summary": cap.get("summary"),
        "domain_tags": cap.get("domain_tags"),
        "collection": cap.get("collection"),
        "scope": cap.get("scope"),
    }
    if cap.get("required_params") is not None or cap.get("optional_params") is not None:
        out["required_params"] = cap.get("required_params") or []
        out["optional_params"] = cap.get("optional_params") or []
    if cap.get("reference"):
        out["reference"] = True
        out["origin"] = cap.get("origin")
        out["source_ref"] = cap.get("source_ref")
        out["note"] = (
            f"Reference knowledge extracted from {cap.get('origin')} — describes the "
            f"approach + inputs; not runnable via {cap.get('origin')}. Implement with "
            f"ABA capabilities (or a lakeFS solution later; source_ref points to the "
            f"original implementation)."
        )
    elif cap.get("archetype") == "r_package":
        r = (cap.get("provisioning") or {}).get("r") or {}
        out["r_source"] = r.get("source")
        out["library"] = r.get("library") or r.get("package")
        if r.get("ref"):
            out["ref"] = r.get("ref")
        out["note"] = (f"R package ({r.get('source')}). ensure_capability installs it into "
                       f"the project R library; then `library({out['library']})` in run_r.")
    else:
        if cap.get("version"):
            out["version"] = cap.get("version")
        if cap.get("import_path"):
            out["import_path"] = cap.get("import_path")
        out["note"] = "Use ensure_capability to make it ready, then use it in run_python."
    return out


def _py_inspect_code(name: str, focus: Optional[str]) -> str:
    """Python introspection script: version/doc, exported symbols, signatures,
    and (optional) detail on one focus object."""
    foc = focus or ""
    return (
        "import importlib, inspect, json\n"
        f"try:\n    m = importlib.import_module({name!r})\n"
        "except Exception as e:\n    print('IMPORT_ERROR:', e); raise SystemExit(0)\n"
        "names = [n for n in dir(m) if not n.startswith('_')]\n"
        "out = {'name': getattr(m,'__name__',%r), 'version': getattr(m,'__version__',None),\n"
        "       'doc': (m.__doc__ or '')[:400], 'symbols': names[:80]}\n"
        "sigs = {}\n"
        "for n in names[:60]:\n"
        "    try:\n        o = getattr(m, n)\n        if callable(o): sigs[n] = str(inspect.signature(o))\n"
        "    except Exception: pass\n"
        "out['signatures'] = sigs\n"
        f"foc = {foc!r}\n"
        "if foc:\n"
        "    o = getattr(m, foc, None)\n"
        "    if o is not None:\n"
        "        try: fs = str(inspect.signature(o))\n"
        "        except Exception: fs = '(...)'\n"
        "        out['focus'] = {'name': foc, 'signature': fs, 'doc': (getattr(o,'__doc__','') or '')[:800]}\n"
        "print(json.dumps(out, indent=1, default=str))\n"
    ) % (name,)


def _r_inspect_code(name: str, focus: Optional[str]) -> str:
    """R introspection script: exports, vignette list, and (optional) focus
    detail — function args, or R6 generator methods/fields."""
    foc = focus or ""
    return (
        f'pkg <- {name!r}\n'
        'ok <- suppressWarnings(suppressMessages(require(pkg, character.only=TRUE)))\n'
        'if (!ok) {{ cat("LOAD_ERROR: package not available/loadable\\n"); quit(status=0) }}\n'
        'cat("== exports ==\\n"); print(utils::head(sort(ls(paste0("package:", pkg))), 120))\n'
        'cat("== vignettes ==\\n"); v <- vignette(package=pkg)\n'
        'if (NROW(v$results)) print(v$results[, "Item"]) else cat("(none)\\n")\n'
        f'foc <- {foc!r}\n'
        'if (nzchar(foc) && exists(foc)) {{\n'
        '  obj <- get(foc); cat("== ", foc, " ==\\n")\n'
        '  if (inherits(obj, "R6ClassGenerator")) {{\n'
        '    cat("R6 public methods:\\n"); print(names(obj$public_methods))\n'
        '    cat("R6 public fields:\\n"); print(names(obj$public_fields))\n'
        '  }} else if (is.function(obj)) {{ print(args(obj)) }}\n'
        '}}\n'
    ).replace("{{", "{").replace("}}", "}")


def _ctx_thread(ctx: dict | None) -> str:
    return (ctx or {}).get("thread_id") or "default"


def list_entities_tool(input_: dict, ctx: dict | None = None) -> dict:
    from core.graph.entities import list_entities
    ents = list_entities(
        exclude_workspace=True, include_archived=False,
        type_filter=(input_.get("type") or None),
        title_query=(input_.get("query") or None),
        limit=int(input_.get("limit") or 30),
    )
    return {"entities": [
        {"id": e["id"], "type": e["type"], "title": e["title"],
         "pinned": e.get("pinned"), "status": e.get("status")}
        for e in ents
    ]}
