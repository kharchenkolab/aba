"""Bio's content-provided services for the ``core/services`` seam — so core can
ask bio for values it needs (code-language sniffing for exec backfill; the host
tool catalog for the recovery report) without ``core/`` importing ``content/``.

Registered at import (pulled in by ``content/bio/__init__.py``). The actual bio
imports are deferred to call time so this module is import-order-safe.
"""
from __future__ import annotations

from core.services import register_service


def _language_sniffer(code: str) -> str:
    """R signals beat python signals; default to python on tie. (Wraps the
    scenarios heuristic; imported lazily.)"""
    from content.bio.lifecycle.scenarios import _detect_language
    return _detect_language(code)


def _host_tool_names():
    """The host's agent-visible tool names — the live MCP catalog if the gateway
    has booted, else a scan of aba_core's tool modules, plus the run_* workhorses.
    Returns a set, or None if nothing could be enumerated."""
    names: set[str] = set()
    try:
        from content.bio.tools import TOOL_SCHEMAS
        names.update(t["name"] for t in TOOL_SCHEMAS if isinstance(t, dict) and t.get("name"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import re
        from pathlib import Path
        tools_dir = Path(__file__).resolve().parent / "mcp_servers/aba_core/tools"
        for f in tools_dir.glob("*.py"):
            for line in f.read_text().splitlines():
                m = re.match(r"\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line)
                if m and not m.group(1).startswith("_"):
                    names.add(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    names.update({"run_python", "run_r"})
    return names or None


def _plan_orientation_preamble(project_id: str, thread_id: str) -> str:
    """Workspace-orientation block for a just-presented plan: canonical dataset
    paths + prior-run files reachable from the new Run's cwd, so the agent uses
    real paths on its first run_python instead of guessing. Composes bio's own
    run-workspace privates — the orchestrator (guide) asks for this through the
    ``core/services`` seam instead of importing them (modularity_audit3 Item 1)."""
    from content.bio.tools.run_exec import _prior_run_files_preamble, _run_scratch_cwd
    from content.bio.lifecycle.runs import active_run_id
    pid, tid = str(project_id), str(thread_id)
    return _prior_run_files_preamble(
        pid, tid,
        current_run_id=active_run_id(tid),
        cwd=_run_scratch_cwd(pid, tid),
    )


def _result_cascade_members(result_id: str) -> set:
    """Containment set for `cascade=members` on a Result delete (moved from main.py,
    Item 2A.4): every figure/table/cell member referenced from metadata.members, plus
    each member's full revision chain (active + superseded). The Result id itself is
    NOT included — it's deleted separately. This is bio-domain knowledge (Result/figure/
    revision semantics + the "figure"/"table" types), so the platform delete route asks
    for it through the core/services seam instead of naming bio types itself.

    Why include superseded revisions: deleting a Result should take its whole history
    with it (superseded revisions are figure entities from make_revision, referenced
    nowhere visible; left behind they look like a leak)."""
    from content.bio.graph.figure_history import figure_history
    from core.graph.entities import get_entity
    out: set = set()
    r = get_entity(result_id)
    if not r:
        return out
    members = (r.get("metadata") or {}).get("members") or []
    member_ids = [m.get("ref") for m in members if isinstance(m, dict) and m.get("ref")]
    for mid in member_ids:
        m = get_entity(mid)
        if not m:
            continue
        out.add(mid)
        # Expand revision chains for figure/table members. Cells don't currently form
        # revision chains via wasRevisionOf, but figure_history is safe on any type.
        if m.get("type") in ("figure", "table"):
            try:
                chain = figure_history(mid, include_superseded=True)
                for e in chain:
                    if e and e.get("id"):
                        out.add(e["id"])
            except Exception:  # noqa: BLE001 — chain walk is best-effort
                pass
    return out


def _aba_intent(intent: dict, refs: dict, ctx=None) -> dict:
    """Execute a CONTENT (lifecycle) `aba.*` write intent — dispatched by
    core.exec.run.harvest_intents for verbs core doesn't handle generically
    (promote/finding/claim/register_dataset). Calls the same `*_tool` logic the
    JSON tools call (so semantics + provenance are identical), resolving local
    'aba:new:N' refs first. Returns {'verb','id'} or {'verb','error'}. This is what
    lets the WHOLE contact plane flip to the library — content extends aba's verbs
    via registration, no core edit (tool_library Phase 3 / follow-on (a))."""
    from content.bio.tools.curation import (
        promote_to_result_tool, create_finding_tool, create_claim_tool, register_dataset_tool)
    v = intent.get("verb")

    def rr(x):  # resolve a local ref (or list) to a real id
        if isinstance(x, list):
            return [refs.get(i, i) for i in x]
        return refs.get(x, x)

    try:
        if v == "promote":
            r = promote_to_result_tool({"figure_id": rr(intent.get("figure")),
                                        "interpretation": intent.get("interpretation", ""),
                                        "title": intent.get("title")}, ctx)
        elif v == "finding":
            r = create_finding_tool({"result_ids": rr(intent.get("result_ids") or []),
                                     "text": intent.get("text", ""),
                                     "title": intent.get("title")}, ctx)
        elif v == "claim":
            r = create_claim_tool({"statement": intent.get("statement", ""),
                                   "evidence_ids": rr(intent.get("evidence_ids") or []),
                                   "negative": bool(intent.get("negative"))}, ctx)
        elif v == "register_dataset":
            r = register_dataset_tool({"title": intent.get("title"),
                                       "path": intent.get("path"), "paths": intent.get("paths"),
                                       "summary": intent.get("summary"),
                                       "source": intent.get("source"),
                                       "organism": intent.get("organism")}, ctx)
        else:
            return {"verb": v, "error": f"unknown lifecycle verb {v!r}"}
        if isinstance(r, dict) and r.get("error"):
            return {"verb": v, "error": r["error"]}
        # id key varies by op (result_id/finding_id/claim_id/dataset_id/…)
        eid = None
        if isinstance(r, dict):
            eid = (r.get("id") or r.get("entity_id") or r.get("result_id")
                   or r.get("finding_id") or r.get("claim_id") or r.get("dataset_id"))
        return {"verb": v, "id": eid, "result": r}
    except Exception as e:  # noqa: BLE001
        return {"verb": v, "error": str(e)}


# Content-provided in-kernel verbs: bio attaches its lifecycle verbs onto the generic
# `aba` object (which core injects). Source string (runs in the kernel after aba=_Aba();
# uses aba.emit_intent → the aba_intent dispatch above). This is the kernel-side twin of
# the services seam: core names no bio concept; bio contributes promote/finding/claim/
# register_dataset here, so the WHOLE contact plane is aba.* and stays seam-clean.
_ABA_KERNEL_VERBS = '''
def promote(figure, interpretation, title=None):
    """Promote a figure to a Result with a written interpretation. Returns a local ref."""
    return aba.emit_intent("promote", figure=figure, interpretation=interpretation, title=title)
def finding(result_ids, text, title=None):
    """Draft a Finding citing one or more results as evidence. Returns a local ref."""
    return aba.emit_intent("finding", result_ids=result_ids, text=text, title=title)
def claim(statement, evidence_ids=None, negative=False):
    """Draft a Claim supported by evidence. Returns a local ref."""
    return aba.emit_intent("claim", statement=statement, evidence_ids=evidence_ids or [], negative=negative)
def register_dataset(title, path=None, paths=None, summary=None, source=None, organism=None):
    """Register a file/folder as a Dataset entity (with file adoption). Returns a local ref."""
    return aba.emit_intent("register_dataset", title=title, path=path, paths=paths,
                           summary=summary, source=source, organism=organism)
aba.promote = promote
aba.finding = finding
aba.claim = claim
aba.register_dataset = register_dataset
# guard: these types need lifecycle wiring — aba.create refuses them, redirecting here.
aba._lifecycle_verbs = {"result": "promote", "finding": "finding", "claim": "claim", "dataset": "register_dataset"}
aba._extra_help = ("LIFECYCLE (use these, not create()): aba.promote(figure, interpretation) -> result; "
                   "aba.finding(result_ids, text); aba.claim(statement, evidence_ids=); "
                   "aba.register_dataset(title, path=/paths=).")
'''


def _aba_kernel_verbs() -> str:
    """Python source (bio's lifecycle verbs) appended to the kernel's aba setup."""
    return _ABA_KERNEL_VERBS


register_service("language_sniffer", _language_sniffer)
register_service("host_tool_names", _host_tool_names)
register_service("plan_orientation_preamble", _plan_orientation_preamble)
register_service("result_cascade_members", _result_cascade_members)
register_service("aba_intent", _aba_intent)
register_service("aba_kernel_verbs", _aba_kernel_verbs)
