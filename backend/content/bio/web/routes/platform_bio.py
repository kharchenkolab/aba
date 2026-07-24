"""Platform-shaped but BIO-AWARE routes, moved out of main.py (Item 2A.4).

These endpoints look domain-neutral but reach into the bio content pack (search
index, feedback stash, adaptive probe, manifest card builders, bundle-scoped
skills, the bio files tree), so they belong in the bio web layer rather than
under core/web (where the seam forbids content imports).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from content.bio.lifecycle.adaptive import run_probe

router = APIRouter()


@router.get("/api/search")
def search_endpoint(q: str = "", limit: int = 25):
    """Faceted search across entities + chat snippets (M9 fallback recovery)."""
    from content.bio.graph.search import search as _search
    return _search(q, limit=limit)


class ClientContextIn(BaseModel):
    context: dict = {}


@router.post("/api/feedback/client-context")
def feedback_client_context(payload: ClientContextIn):
    """Stash a browser-side context snapshot (recent console errors, route, UI
    state) so Guide can read it via read_client_context when filing a bug report
    — Guide can't see the browser otherwise. Captured on bug-button click; not
    sent anywhere until the user files a report. No project context needed."""
    from content.bio.tools.feedback import stash_client_context
    stash_client_context(payload.context)
    return {"ok": True}


@router.post("/api/run-probe")
async def trigger_probe():
    """
    Run one pop-quiz probe (§3.6). Normally a background cron; exposed as an
    endpoint so it can be triggered on demand / tested. Non-blocking work
    runs in a thread.
    """
    report = await asyncio.get_event_loop().run_in_executor(None, run_probe)
    if report is None:
        return {"ran": False, "reason": "no probeable entities yet"}
    return {"ran": True, **report}


@router.get("/api/manifest/preview")
def manifest_preview(focus_entity_id: str | None = None, thread_id: str | None = None):
    """Build a Manifest live for the current (focus, thread) without
    running a turn. This is what the Drawer hits whenever the user
    refocuses or switches threads — so the panel reflects what the agent
    WOULD see if they sent a message right now, not the last turn's
    snapshot.

    No persistence; the row only gets written when an actual turn runs.
    """
    from core.manifest.assembler import build_manifest
    import content.bio.cards  # noqa: F401 — ensure per-type builders register
    # Tolerate the "default" sentinel used elsewhere in the UI.
    resolved_thread = None
    if thread_id and thread_id != "default":
        resolved_thread = thread_id
    m = build_manifest(
        session_id="preview",
        turn_index=0,
        focus_entity_id=focus_entity_id,
        thread_id=resolved_thread,
    )
    return {"manifest": m.to_dict()}


@router.post("/api/skills/reload")
def skills_reload():
    """Re-resolve the bundle scope chain and re-project its skills into the live
    catalog (system + installation + lab + user). Lets a `git pull` in any
    scope's bundle (or an edited vendor skill) take effect without a backend
    bounce.

    Why an explicit endpoint instead of relying on uvicorn's --reload watcher:
    the institution/lab/user bundles live outside the source tree (or behind
    --reload-exclude), so edits there don't bounce uvicorn — but that also means
    new content doesn't propagate. This endpoint is the manual refresh seam.

    Response includes per-scope counts so operators can confirm the scope they
    expected to load actually loaded."""
    from core.skills.loader import _REGISTRY, list_skills
    from core.bundle.active import reload_bundle
    from content.bio.skills import register_from_bundle
    before = len(_REGISTRY)
    reload_bundle()                       # re-resolve scope chain + re-compose
    by_scope = register_from_bundle(clear=True)
    sk = list_skills()
    return {
        "status": "ok",
        "before": before,
        "after": len(sk),
        "by_scope": by_scope,             # {scope_name: skill count}
        "always": sum(1 for s in sk if s.visibility == "always"),
        "local":  sum(1 for s in sk if s.visibility != "always"),
    }


@router.post("/api/projects/{pid}/materialize")
def project_materialize(pid: str, clean: bool = False, include_archived: bool = False):
    """Build projects/<pid>/files/ as a navigable folder tree on disk
    (files.md §8). Symlinks where supported, copies on systems that
    can't. Idempotent — running it twice converges.
    """
    from core.files.materialize import materialize_tree
    from content.bio.files.tree import build_files_tree
    from content.bio.web.routes.files import _run_backed_path
    from core import projects
    out = projects.PROJECTS_DIR / pid / "files"
    tree = build_files_tree(include_archived=include_archived)
    # resolver for ledger-sourced run outputs: their artifact_path is a server
    # URL; the bytes live in the substrate workspace (kernel jobdir / retained
    # tree). Without this, every kernel-run output materializes as "missing".
    summary = materialize_tree(tree, out, clean=clean, resolve=_run_backed_path)
    return summary
