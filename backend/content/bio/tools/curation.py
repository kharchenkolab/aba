"""Bio curation cluster — entity-lifecycle gestures: pin/promote,
findings/claims, dataset register/add/remove, run open/close, reference
register/find, annotate/archive (WU-3-tail).

The agent's highest-volume "this matters" gestures live here. Includes
all the dataset-management helpers (_resolve_dataset_path,
_bundle_paths_into_data_dir, _hardlink_tree, _adopt_into_data_dir,
_dataset_layout_hint, _compound_ext, _within, _scratch_bases) — they're
only used by the dataset tools."""

from __future__ import annotations
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

# Cross-cluster helpers — _ctx_thread is in ctx_read (alongside the other
# small ctx helpers); register_dataset / promote_to_result / create_claim /
# open_run / close_run all need it for thread_id resolution. Imported
# explicitly so the bare-name reference in this file resolves.
# open_run_tool also needs run_exec's kernel-CWD helpers (it shifts the
# kernel's cwd into the new run's directory + emits the prior-files
# preamble — same shape as run_python's first call after a cwd switch).
from .ctx_read import _ctx_thread
from .run_exec import _run_scratch_cwd, _prior_run_files_preamble


def _infer_scope(input_: dict, ctx: dict | None) -> str:
    """Default-by-signal placement (refs.md §3.3): an explicit scope always
    wins; a linked cluster path → group (shared data is lab-reusable); a freshly
    built/derived artifact registered while a run is open → project; otherwise
    personal. promotion moves it wider later if reuse appears."""
    if input_.get("scope"):
        return input_["scope"]
    if input_.get("mode") == "link":
        return "group"
    tid = (ctx or {}).get("thread_id")
    if tid:
        try:
            from content.bio.lifecycle.runs import active_run_id
            if active_run_id(tid):
                return "project"
        except Exception:  # noqa: BLE001
            pass
    return "personal"


def register_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Keep a file/dir as a reusable reference. mode='copy' (default) owns the
    bytes (content-addressed); mode='link' adopts a pre-existing path in place
    (no copy) — for large cluster reference stores. Scope defaults by signal
    (see _infer_scope); pass scope explicitly to override."""
    path = input_.get("path")
    if not path:
        return {"error": "path is required"}
    from core.data import register_reference as _reg, get_reference
    requested = input_.get("scope")
    scope = _infer_scope(input_, ctx)
    try:
        eid = _reg(path, organism=input_.get("organism"), role=input_.get("role"),
                   source=input_.get("source"), assembly=input_.get("assembly"),
                   derived_from=input_.get("derived_from"),
                   version=input_.get("version"), mode=input_.get("mode", "copy"),
                   scope=scope)
    except Exception as e:  # noqa: BLE001
        return {"error": f"register failed: {e}"}
    d = get_reference(eid) or {}
    ident = d.get("identity") or {}
    owned = d.get("owned", True)
    actual = d.get("scope")
    resp = {"status": "ok", "reference_id": eid, "sha": ident.get("sha"),
            "owned": owned, "scope": actual, "organism": d.get("organism"),
            "role": d.get("role"), "structural_path": d.get("structural_path"),
            "artifact_path": d.get("artifact_path"),
            "note": ("Owned content-addressed copy (deduplicated)." if owned
                     else "Linked in place — no copy.") + " Reuse via find_reference."}
    if requested and actual and actual != requested:
        resp["warning"] = (f"no write access to the {requested!r} tier; stored at "
                           f"{actual!r} — ask a curator to promote it")
    return resp


def find_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Find a stored reference by organism/role before fetching/building (P4)."""
    from core.data import find_reference as _find, list_references as _list
    if input_.get("all"):
        return {"references": _list(organism=input_.get("organism"), role=input_.get("role"),
                                    assembly=input_.get("assembly"))}
    r = _find(organism=input_.get("organism"), role=input_.get("role"),
              assembly=input_.get("assembly"))
    return {"found": bool(r), "reference": r}


def promote_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Promote a reference up a tier (project → group → institution) so it's
    shared more widely — e.g. when a project reference proves reusable lab-wide.
    Gated in practice by write access to the destination tier (institution is
    curator-only). `scope` is the destination tier."""
    ref_id = input_.get("reference_id")
    to = input_.get("scope") or input_.get("to_scope")
    if not ref_id or not to:
        return {"error": "reference_id and scope (destination tier) are required"}
    from core.data import promote_reference
    try:
        return {"status": "ok", **promote_reference(ref_id, to)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"promote failed: {e}"}


def describe_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Inspect a stored reference: facets, content identity, lineage,
    structural path, and acquisition provenance (how it was obtained)."""
    ref_id = input_.get("reference_id")
    if not ref_id:
        return {"error": "reference_id is required"}
    from core.data import get_reference
    d = get_reference(ref_id)
    if d:
        return {"found": True, "reference_id": d.get("id"), "title": d.get("title"),
                "organism": d.get("organism"), "assembly": d.get("assembly"),
                "role": d.get("role"), "structural_path": d.get("structural_path"),
                "owned": d.get("owned"), "sha": (d.get("identity") or {}).get("sha"),
                "identity": d.get("identity"), "acquisition": d.get("acquisition"),
                "derivation": d.get("derivation"), "scope": d.get("scope"),
                "artifact_path": d.get("artifact_path")}
    # Legacy reference without a descriptor → fall back to the entity.
    from core.graph.entities import get_entity
    e = get_entity(ref_id)
    if not e:
        return {"found": False, "error": f"unknown reference {ref_id}"}
    meta = e.get("metadata") or {}
    return {"found": True, "reference_id": ref_id, "title": e.get("title"),
            "organism": meta.get("organism"), "assembly": meta.get("assembly"),
            "role": meta.get("role"), "structural_path": meta.get("structural_path"),
            "sha": meta.get("sha"), "artifact_path": e.get("artifact_path"),
            "legacy": True}


def resolve_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Resolve a stored reference to a local path for use in a run and pin the
    run-lock (a schema-legal `run --used--> reference` edge + the content-sha
    version lock), so the run records exactly which reference version it
    consumed (refs.md §9). Local tier: the store path IS the local path —
    nothing to stage; the actual staging (mount/download) is the seam that
    activates for object-store / remote tiers. Resolve by `reference_id` or by
    organism/role/assembly facets."""
    from core.data import get_reference, find_reference
    ref_id = input_.get("reference_id")
    if not ref_id:
        r = find_reference(organism=input_.get("organism"), role=input_.get("role"),
                           assembly=input_.get("assembly"))
        ref_id = (r or {}).get("id")
        if not ref_id:
            return {"found": False,
                    "error": "no matching reference — fetch or build it first"}
    d = get_reference(ref_id)
    if d:
        path, sha, sp = d.get("artifact_path"), (d.get("identity") or {}).get("sha"), d.get("structural_path")
    else:  # legacy reference without a descriptor → fall back to the entity
        from core.graph.entities import get_entity
        e = get_entity(ref_id)
        if not e:
            return {"error": f"unknown reference {ref_id}"}
        meta = e.get("metadata") or {}
        path, sha, sp = e.get("artifact_path"), meta.get("sha"), meta.get("structural_path")
    if not path:
        return {"error": f"reference {ref_id} has no resolvable path"}

    run_id = None
    pinned = False
    tid = (ctx or {}).get("thread_id")
    if tid:
        from content.bio.lifecycle.runs import active_run_id
        run_id = active_run_id(tid)
        if run_id:
            try:
                from core.graph.edges import add_edge
                add_edge(run_id, ref_id, "used")
                pinned = True
            except Exception:  # noqa: BLE001 — provenance pin is best-effort
                pass
    return {"status": "ok", "reference_id": ref_id, "local_path": path,
            "version_lock": sha, "structural_path": sp, "run_id": run_id,
            "note": ("Pinned: run used this reference@sha." if pinned
                     else "No open run — returned the path without a run-lock.")}


def _unpack(path: str, kind: str) -> str:
    """Unpack a fetched archive next to itself; return the unpacked dir (or the
    original path if `kind` is falsy / unrecognized)."""
    import tarfile
    import zipfile
    from pathlib import Path as _P
    p = _P(path)
    if not kind:
        return path
    out = p.parent / (p.name + ".unpacked")
    out.mkdir(parents=True, exist_ok=True)
    if kind == "zip":
        with zipfile.ZipFile(p) as z:
            z.extractall(out)
    elif kind in ("tar.gz", "tgz", "tar"):
        with tarfile.open(p) as t:
            t.extractall(out)
    elif kind == "gz":
        import gzip
        import shutil as _sh
        dst = out / p.stem
        with gzip.open(p, "rb") as fi, open(dst, "wb") as fo:
            _sh.copyfileobj(fi, fo)
    else:
        return path
    # If the archive expanded to a single top dir, return that (cleaner catalog).
    kids = [c for c in out.iterdir()]
    return str(kids[0]) if len(kids) == 1 and kids[0].is_dir() else str(out)


def fetch_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Fetch a reference / pre-built index from a known provider (refs.md §5.1)
    and register it with a re-runnable spec. Distinct from lookup_sra_runinfo
    (sequencing reads). `provider` + facets (or an `accession` for template
    providers) resolve to an asset via the reference-source catalog."""
    provider = input_.get("provider")
    if not provider:
        return {"error": "provider is required (e.g. aws-indexes, ncbi)"}
    from core.data.refsources import resolve_asset
    from core.data import register_reference, get_reference
    try:
        asset = resolve_asset(provider, organism=input_.get("organism"),
                              assembly=input_.get("assembly"), role=input_.get("role"),
                              accession=input_.get("accession"),
                              filename=input_.get("filename"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"resolve failed: {e}"}

    # URL-based asset → fetch + unpack + register now.
    if asset.get("url"):
        from content.bio.tools.discovery import fetch_url
        fr = fetch_url({"url": asset["url"]}, ctx)
        if fr.get("status") != "ok":
            return {"error": f"fetch failed: {fr.get('error') or fr}", "url": asset["url"]}
        path = fr["path"]
        if asset.get("unpack"):
            try:
                path = _unpack(path, asset["unpack"])
            except Exception as e:  # noqa: BLE001
                return {"error": f"unpack failed: {e}", "path": fr["path"]}
        spec = {"mode": "fetch", "provider": provider, "url": asset["url"],
                "version": asset.get("version"), "unpack": asset.get("unpack")}
        try:
            eid = register_reference(
                path, organism=asset.get("organism") or input_.get("organism"),
                assembly=asset.get("assembly") or input_.get("assembly"),
                role=asset.get("role") or input_.get("role"),
                source=provider, version=asset.get("version"), acquisition=spec,
                scope=input_.get("scope") or "group")  # fetched standard refs → lab-shared
        except Exception as e:  # noqa: BLE001
            return {"error": f"register failed: {e}"}
        d = get_reference(eid) or {}
        return {"status": "ok", "reference_id": eid, "owned": d.get("owned"),
                "structural_path": d.get("structural_path"), "source": provider,
                "note": "Fetched + registered (re-runnable spec recorded)."}

    # Template/CLI asset → hand the agent the command to run (Phase 0: not auto-run).
    if asset.get("command"):
        return {"status": "manual", "provider": provider,
                "command": asset["command"], "unpack": asset.get("unpack"),
                "version": asset.get("version"),
                "note": (f"Run this command (needs the provider's CLI), then "
                         f"register_reference(path=<output>, source='{provider}', "
                         f"version='{asset.get('version')}', role='{asset.get('role')}'). "
                         f"The CLI is not auto-run in Phase 0.")}
    return {"error": "resolved asset had neither a url nor a command"}


def _within(p: str, base: str) -> bool:
    import os
    p, base = os.path.abspath(p), os.path.abspath(base)
    return p == base or p.startswith(base + os.sep)


def _scratch_bases(ctx: dict | None) -> list[str]:
    """Where a relative-path download actually lands: the active Run's output
    dir and the thread's scratch dir (the kernel cwd), so register_dataset can
    find files the agent wrote there with a bare name."""
    bases: list[str] = []
    try:
        from core.data.workspace import scratch_dir
        from core import projects
        tid = _ctx_thread(ctx)
        pid = projects.current() or "default"
        if tid:
            from content.bio.lifecycle.runs import active_run_id
            from core.graph.entities import get_entity
            rid = active_run_id(tid)
            if rid:
                ap = (get_entity(rid) or {}).get("artifact_path")
                if ap:
                    bases.append(str(ap))
            bases.append(str(scratch_dir(pid, f"thread-{tid}")))
    except Exception:  # noqa: BLE001
        pass
    return bases


def _hardlink_tree(src: str, dest: str) -> None:
    """Replicate src→dest by HARDLINKING every file (instant — no data copied;
    both names point at the same inodes, so the dest survives scratch GC of the
    src). Raises OSError (EXDEV) across filesystems → caller falls back to copy."""
    import os
    if os.path.isfile(src):
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        os.link(src, dest)
        return
    os.makedirs(dest, exist_ok=True)
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        dst_root = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(dst_root, exist_ok=True)
        for f in files:
            os.link(os.path.join(root, f), os.path.join(dst_root, f))


def _adopt_into_data_dir(src: str) -> tuple[str, bool]:
    """Bring a scratch path into DATA_DIR so a registered dataset persists past
    scratch GC. Hardlinks (instant, non-blocking) when same-filesystem; across
    filesystems, copies in a BACKGROUND thread (via a .part temp + atomic rename)
    so the turn never blocks. Returns (dest_path, materializing).

    Targets the **active project's** data dir (``projects/<pid>/data/``), same
    as ``_bundle_paths_into_data_dir`` — NOT the module-level workspace
    ``config.DATA_DIR``, which the agent doesn't see via its kernel
    ``os.environ["DATA_DIR"]``. Pre-fix this misaligned: a scratch file would
    get hardlinked into the workspace dir, agent's ``os.listdir(DATA_DIR)``
    returned empty, and the agent reported "no dataset registered" — same
    bug-shape as the 2026-05-31 bundle fix."""
    import os, shutil, threading
    from core.config import current_project_id, project_data_dir
    target_data = project_data_dir(current_project_id())
    src = os.path.abspath(src)
    dest = os.path.join(str(target_data), os.path.basename(src.rstrip("/")) or "dataset")
    if not os.path.exists(os.path.dirname(dest)):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    base, i = dest, 2
    while os.path.exists(dest):       # don't clobber an existing dataset dir
        dest = f"{base}_{i}"; i += 1
    try:
        _hardlink_tree(src, dest)     # instant on same fs
        return dest, False
    except OSError:                   # cross-device → background copy
        shutil.rmtree(dest, ignore_errors=True)
        tmp = dest + ".part"

        def _bg():
            try:
                shutil.rmtree(tmp, ignore_errors=True)
                if os.path.isdir(src):
                    shutil.copytree(src, tmp)
                else:
                    os.makedirs(os.path.dirname(tmp) or ".", exist_ok=True)
                    shutil.copy2(src, tmp)
                os.replace(tmp, dest)
            except Exception:  # noqa: BLE001
                shutil.rmtree(tmp, ignore_errors=True)

        threading.Thread(target=_bg, daemon=True).start()
        return dest, True


def _resolve_dataset_path(path: str, ctx: dict | None) -> str:
    """Resolve a bare/relative path against the scratch tier first (where the
    agent's relative downloads land), then the **per-project** data dir (what
    the kernel preamble sets ``os.environ["DATA_DIR"]`` to — the dir the agent
    sees), then the module-level workspace DATA_DIR (back-compat for the
    no-project case), then process cwd. Returns the first existing match, or
    the most likely candidate if nothing exists.

    The per-project candidate is the load-bearing one: the module-level
    ``config.DATA_DIR`` constant binds at import to the workspace dir
    (``projects/_workspace/data``) and never tracks which project is active,
    so it diverges from the agent's view in any real install. Pre-2026-06-09
    this caused the agent's natural "save to DATA_DIR/foo then register 'foo'"
    pattern to fail with "Nothing to register"."""
    import os
    from core.config import DATA_DIR
    from core.config import current_project_id, project_data_dir
    if os.path.isabs(path):
        return os.path.normpath(path)        # also collapses `./` segments
    cands = [os.path.normpath(os.path.join(b, path)) for b in _scratch_bases(ctx)]
    cands.append(os.path.normpath(os.path.join(str(project_data_dir(current_project_id())), path)))
    cands.append(os.path.normpath(os.path.join(str(DATA_DIR), path)))
    cands.append(os.path.normpath(os.path.abspath(path)))
    return next((c for c in cands if os.path.exists(c)), cands[0])


def _bundle_paths_into_data_dir(srcs: list[str], title: str) -> tuple[str, list[str], list[str]]:
    """Link a specific list of files into a fresh `DATA_DIR/<slug>/` directory.
    Used by register_dataset's multi-path mode so the agent can register only
    the files it actually wants (a 10x triplet, say) without dragging derivative
    files (.h5ad, .pkl) that happen to sit in the same dir.

    Targets the **active project's** data dir (`projects/<pid>/data/`) — NOT
    the module-level `config.DATA_DIR` constant, which resolves once at import
    to the WORKSPACE-level data dir. The kernel's `os.environ['DATA_DIR']` is
    the per-project path, so a workspace-level bundle would land somewhere the
    agent can't see (the 2026-05-31 live bug where the agent reported "the
    dataset hasn't been registered in DATA_DIR yet" because its `os.listdir`
    on the project DATA_DIR returned empty).

    Returns (bundle_path, present_paths, missing_paths). Hardlinks when on the
    same filesystem; copies otherwise. Numeric suffix on collision."""
    import os, re, shutil
    from core.config import current_project_id, project_data_dir
    pid = current_project_id() or "default"
    data_dir = project_data_dir(pid)
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (title or "dataset")).strip("_") or "dataset"
    dest = os.path.join(str(data_dir), slug)
    base, i = dest, 2
    while os.path.exists(dest):
        dest = f"{base}_{i}"; i += 1
    os.makedirs(dest, exist_ok=True)
    present, missing = [], []
    for s in srcs:
        if not os.path.isfile(s):
            missing.append(s); continue
        present.append(s)
        target = os.path.join(dest, os.path.basename(s))
        try:
            os.link(s, target)               # instant hardlink on same fs
        except OSError:
            shutil.copy2(s, target)          # cross-fs fallback
    return dest, present, missing


def register_dataset_tool(input_: dict, ctx: dict | None = None) -> dict:
    import os
    from core.config import DATA_DIR
    from core.config import (WORK_DIR, current_project_id, project_data_dir,
                             project_work_dir)
    # Per-project equivalents of DATA_DIR / WORK_DIR — what the agent actually
    # sees via os.environ. The module-level constants are workspace-level
    # (resolved once at import) and miss in any real install. Used for the
    # adopt-check (so a file in the per-project work tier is recognized as
    # scratch and adopted into per-project data) AND for the error message
    # (so the agent is sent to the same DATA_DIR it sees).
    _pid = current_project_id()
    _PROJECT_DATA = str(project_data_dir(_pid))
    _PROJECT_WORK = str(project_work_dir(_pid))
    title = input_.get("title")
    if not title:
        return {"error": "title is required"}
    paths_list = input_.get("paths")
    path = input_.get("path")
    if not paths_list and not path:
        return {"error": "either `path` (single file/dir) or `paths` (list of files) is required"}
    if paths_list and path:
        return {"error": "pass `path` OR `paths`, not both"}

    from core.graph.entities import create_entity, update_entity

    bundle_note = ""
    if paths_list:
        # Multi-path mode: resolve each, link them into a fresh DATA_DIR/<slug>/
        # bundle, register THAT bundle. Lets the agent register only the files
        # it actually wants without dragging derivatives that sit nearby on disk.
        resolved = [_resolve_dataset_path(str(p), ctx) for p in paths_list]
        try:
            abspath, present, missing = _bundle_paths_into_data_dir(resolved, str(title))
        except Exception as e:  # noqa: BLE001
            return {"error": f"failed to bundle paths into DATA_DIR: {e}"}
        if not present:
            return {"error": "none of the listed paths exist on disk",
                    "missing": missing}
        exists = True
        adopted, materializing = True, False
        if missing:
            bundle_note = (
                f" Bundle has {len(present)} file(s) linked into DATA_DIR; "
                f"{len(missing)} listed path(s) did not exist on disk and were skipped: "
                + ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
            )
        else:
            bundle_note = f" Bundle has {len(present)} file(s) linked into DATA_DIR."
    else:
        abspath = _resolve_dataset_path(str(path), ctx)
        exists = os.path.exists(abspath)
        # Adopt: a file found in the SCRATCH tier (not already under DATA_DIR) is
        # hardlinked into DATA_DIR so the dataset persists past the 48h scratch GC —
        # removing the move-then-re-register dance. Non-blocking (instant hardlink,
        # or a background copy across filesystems).
        adopted = materializing = False
        # "Is this file in scratch?" / "is it already in DATA_DIR?" must check
        # the PER-PROJECT trees the agent actually writes to, plus the module
        # ones for back-compat. Without per-project membership, a scratch file
        # at projects/<pid>/work/... wouldn't be recognized as scratch and the
        # adopt step would silently skip — file gets registered by-reference
        # against a path that'll be GC'd in 48h.
        in_work = (_within(abspath, str(WORK_DIR)) or
                   _within(abspath, _PROJECT_WORK))
        in_data = (_within(abspath, str(DATA_DIR)) or
                   _within(abspath, _PROJECT_DATA))
        if exists and in_work and not in_data:
            try:
                abspath, materializing = _adopt_into_data_dir(abspath)
                adopted = True
            except Exception:  # noqa: BLE001 — fall back to by-reference at the scratch path
                pass
        # Fix 3: a path that doesn't exist can't be registered (the dataset
        # schema requires artifact_path). Return a clear, actionable error
        # instead of letting create_entity raise the cryptic "required field
        # 'artifact_path' is missing or empty" — the symptom when a relative
        # path resolved against a now-gone per-run scratch dir.
        if not exists:
            return {"error": (
                f"Nothing to register: no file or directory found at {path!r} "
                f"(resolved to {abspath}). If you created/downloaded it in a run_python "
                f"call, it may have landed in a per-run scratch dir that no longer exists "
                f"— re-create it under DATA_DIR ({_PROJECT_DATA}) or pass an ABSOLUTE path, then "
                f"register that."
            )}
    summary = (input_.get("summary") or "").strip()
    # Post Cutover 4: `producing_code` is no longer a column on entities.
    # If the caller supplied it (a holdover from the legacy schema), we
    # silently drop it here; agents shouldn't notice. The dataset's code
    # provenance, when meaningful, is reachable via the producing exec
    # record (the agent passes `exec_id` for that, not free-form code).
    from core.graph.derivation import imported
    from content.bio.lifecycle.runs import agent_actor_for_thread
    eid = create_entity(
        entity_type="dataset", title=title,
        artifact_path=abspath if exists else None,
        derivation=imported(input_.get("source") or "external"),   # Phase 2B
        actor=agent_actor_for_thread(_ctx_thread(ctx)),            # Phase 2B
        metadata={"thread_id": _ctx_thread(ctx), "origin": "external",
                  "by_reference": not adopted, "ref_path": abspath,
                  "summary": summary, "source": input_.get("source", ""),
                  "organism": input_.get("organism")})
    if summary:
        # The dataset detail view shows `notes` as the description — populate it.
        update_entity(eid, notes=summary)
    note = "Registered as a Dataset entity — now in the Data facet."
    if adopted and not materializing:
        note += " Files adopted into DATA_DIR (kept past scratch cleanup)."
    elif adopted and materializing:
        note += " Files are copying into DATA_DIR in the background — fully available shortly."
    if bundle_note:
        note += bundle_note
    if not exists:
        note += " WARNING: path not found on disk; registered by reference only — pass a path under DATA_DIR."
    # S3 (2026-06-02): include a one-line layout hint + the canonical path back
    # in the tool_result so the agent's next turn has the location in
    # conversation context. Removes the "I just registered it — now where does
    # it live?" round-trip that was costing 3-5 tool calls (prj_8d699668).
    layout_hint = _dataset_layout_hint(abspath) if exists else ""
    if layout_hint:
        # Persist the hint on the entity so the cwd-shift preamble can surface it later.
        # MERGE with the existing metadata — update_entity REPLACES the metadata
        # column outright, so reading-then-merging is required to keep the
        # bookkeeping fields (by_reference / ref_path / origin / summary)
        # that create_entity just wrote.
        try:
            from core.graph.entities import get_entity as _get_entity_for_md
            cur = ((_get_entity_for_md(eid) or {}).get("metadata") or {})
            update_entity(eid, metadata={**cur, "layout_hint": layout_hint})
        except Exception:  # noqa: BLE001
            pass
    return {"status": "ok", "dataset_id": eid, "title": title,
            "artifact_path": abspath if exists else None,
            "layout_hint": layout_hint or None,
            "note": note}


def _dataset_layout_hint(path: str) -> str:
    """One-line description of what's at this dataset path. Helps the agent's
    next turn pick the right loader without re-inspecting the dir.

    Examples:
      "1 file (.h5ad)"
      "9 flat files (.gz), pattern: GSM*_{barcodes,features,matrix}.{tsv,mtx}.gz"
      "3 sample subdirs (each with 10x triplet)"
    """
    try:
        import os
        if not path or not os.path.exists(path):
            return ""
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            return f"1 file ({ext})" if ext else "1 file"
        entries = []
        try:
            entries = sorted(os.listdir(path))[:50]
        except OSError:
            return ""
        if not entries:
            return "empty directory"
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        dirs = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        if files and not dirs:
            exts = sorted({_compound_ext(f) for f in files if _compound_ext(f)})
            ext_str = ", ".join(exts[:4]) if exts else "various"
            return f"{len(files)} flat file(s) ({ext_str})"
        if dirs and not files:
            return f"{len(dirs)} subdir(s); first: {dirs[0]}/"
        return f"{len(files)} file(s) + {len(dirs)} subdir(s)"
    except Exception:  # noqa: BLE001
        return ""


def _compound_ext(name: str) -> str:
    """Two-suffix extension for .tar.gz / .tsv.gz / .mtx.gz; otherwise one."""
    parts = name.lower().rsplit(".", 2)
    if len(parts) >= 3 and parts[-1] in ("gz", "bz2", "xz", "zip"):
        return "." + parts[-2] + "." + parts[-1]
    return "." + parts[-1] if len(parts) >= 2 else ""


def add_to_dataset_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Hardlink one or more files into an existing directory-shaped
    dataset's bundle directory. The dataset entity itself stays at the
    same id + artifact_path; only the directory contents change. Same
    hardlink-then-cross-fs-copy fallback as `register_dataset`'s
    multi-path bundling."""
    import os, shutil
    from core.graph.entities import get_entity
    dsid = (input_.get("dataset_id") or "").strip()
    paths = input_.get("paths") or []
    if not dsid:
        return {"error": "dataset_id is required"}
    if not isinstance(paths, list) or not paths:
        return {"error": "paths is required (non-empty list)"}
    ent = get_entity(dsid)
    if not ent:
        return {"error": f"dataset {dsid} not found"}
    if ent.get("type") != "dataset":
        return {"error": f"{dsid} is a {ent.get('type')}, not a dataset"}
    dest_dir = ent.get("artifact_path")
    if not dest_dir or not os.path.isdir(dest_dir):
        return {"error": f"dataset {dsid} is not directory-shaped "
                f"(artifact_path={dest_dir!r}) — files can't be added. "
                f"Either register a new dataset bundling the originals + the "
                f"new files, or convert this one to a directory first."}

    resolved = [_resolve_dataset_path(str(p), ctx) for p in paths]
    added, missing, already_present = [], [], []
    for src in resolved:
        if not os.path.isfile(src):
            missing.append(src); continue
        target = os.path.join(dest_dir, os.path.basename(src))
        if os.path.exists(target):
            already_present.append(os.path.basename(src)); continue
        try:
            os.link(src, target)
        except OSError:
            try:
                shutil.copy2(src, target)
            except Exception as e:  # noqa: BLE001
                missing.append(f"{src} (copy failed: {e})"); continue
        added.append(os.path.basename(src))
    note = f"Added {len(added)} file(s) to dataset {dsid}."
    if already_present:
        note += f" Skipped {len(already_present)} already-present: {', '.join(already_present)}."
    if missing:
        note += f" Could not add {len(missing)} file(s): {', '.join(missing)}."
    return {"status": "ok" if added or not missing else "partial",
            "dataset_id": dsid, "added": added,
            "already_present": already_present, "missing": missing,
            "dataset_dir": dest_dir, "note": note}


def remove_from_dataset_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Unlink one or more files from a directory-shaped dataset's bundle.
    Safety: each path must resolve INSIDE the dataset directory; absolute
    paths outside are refused. Accepts basenames (resolved against the
    dataset dir) or absolute paths."""
    import os
    from core.graph.entities import get_entity
    dsid = (input_.get("dataset_id") or "").strip()
    paths = input_.get("paths") or []
    if not dsid:
        return {"error": "dataset_id is required"}
    if not isinstance(paths, list) or not paths:
        return {"error": "paths is required (non-empty list)"}
    ent = get_entity(dsid)
    if not ent:
        return {"error": f"dataset {dsid} not found"}
    if ent.get("type") != "dataset":
        return {"error": f"{dsid} is a {ent.get('type')}, not a dataset"}
    dest_dir = ent.get("artifact_path")
    if not dest_dir or not os.path.isdir(dest_dir):
        return {"error": f"dataset {dsid} is not directory-shaped "
                f"(artifact_path={dest_dir!r}) — nothing to remove from."}
    dest_abs = os.path.realpath(dest_dir)

    removed, not_found, refused = [], [], []
    for p in paths:
        p = str(p).strip()
        if not p:
            continue
        # Basename or absolute? If absolute, must live inside the bundle.
        if os.path.isabs(p):
            cand_abs = os.path.realpath(p)
            if not (cand_abs == dest_abs or cand_abs.startswith(dest_abs + os.sep)):
                refused.append(p); continue
            target = cand_abs
        else:
            target = os.path.join(dest_dir, os.path.basename(p))
        if not os.path.isfile(target):
            not_found.append(os.path.basename(target)); continue
        try:
            os.unlink(target)
            removed.append(os.path.basename(target))
        except OSError as e:
            refused.append(f"{target} (unlink failed: {e})")
    note = f"Removed {len(removed)} file(s) from dataset {dsid}."
    if not_found:
        note += f" Not found: {', '.join(not_found)}."
    if refused:
        note += f" Refused (outside dataset dir or unlink failed): {', '.join(refused)}."
    return {"status": "ok" if not refused else "partial",
            "dataset_id": dsid, "removed": removed,
            "not_found": not_found, "refused": refused,
            "dataset_dir": dest_dir, "note": note}


# pin_entity_tool removed 2026-06-08 (entity-mgmt refactor Phase 1).
# It toggled a legacy `pinned` boolean column that no UI surface has
# read since task #318 unified "pin" semantics around
# promote_to_result / pin_evidence. The actual pin op is
# promote_to_result_tool below (figure → new Result).


def promote_to_result_tool(input_: dict, ctx: dict | None = None) -> dict:
    fid, interp = input_.get("figure_id"), input_.get("interpretation")
    if not fid or not interp:
        return {"error": "figure_id and interpretation are required"}
    from content.bio.lifecycle.promote import promote_figure_to_result
    try:
        rid = promote_figure_to_result(fid, interp, input_.get("title"))
    except ValueError as e:
        return {"error": str(e)}
    try:    # fire the Skeptic review off-thread (mirrors the UI endpoint)
        import threading
        from content.bio.advisors.runner import skeptic_review
        threading.Thread(target=skeptic_review, args=(rid,), daemon=True).start()
    except Exception:  # noqa: BLE001
        pass
    return {"status": "ok", "result_id": rid,
            "note": "Promoted to a Result; the Skeptic advisor is reviewing it."}


def create_finding_tool(input_: dict, ctx: dict | None = None) -> dict:
    rids = list(input_.get("result_ids") or [])
    if not rids:
        return {"error": "result_ids (>=1) are required"}
    from content.bio.lifecycle.promote import promote_results_to_finding
    try:
        fid = promote_results_to_finding(rids, input_.get("text") or "", input_.get("title"))
    except ValueError as e:
        return {"error": str(e)}
    return {"status": "ok", "finding_id": fid}


def create_claim_tool(input_: dict, ctx: dict | None = None) -> dict:
    import datetime as _dt
    stmt = (input_.get("statement") or "").strip()
    if not stmt:
        return {"error": "statement is required"}
    from core.graph.entities import create_entity
    from core.graph.edges import add_edge
    evidence = list(input_.get("evidence_ids") or [])
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    from core.graph.derivation import derived_from, manual
    from content.bio.lifecycle.runs import agent_actor_for_thread
    cid = create_entity(
        entity_type="claim", title=stmt[:80],
        derivation=derived_from(evidence) if evidence else manual(),   # Phase 2B
        actor=agent_actor_for_thread(_ctx_thread(ctx)),                # Phase 2B
        metadata={"statement": stmt, "negative": bool(input_.get("negative")),
                  "evidence_ids": evidence, "caveats": [], "alternatives": [],
                  "confidence": "preliminary", "thread_id": _ctx_thread(ctx),
                  "status_log": [{"from": None, "to": "preliminary", "reason": "created",
                                  "actor": "agent", "at": now}]})
    for rid in evidence:
        try:
            add_edge(cid, rid, "supports")
        except Exception:  # noqa: BLE001
            pass
    return {"status": "ok", "claim_id": cid, "statement": stmt}


def open_run_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Open an analysis Run so this pipeline's outputs group as one unit.

    Surfaces the cwd shift right here in the return so the agent doesn't write
    its first cell with a bare relative path that no longer resolves. Without
    this, the first `run_python` after `open_run` always hit FileNotFoundError
    + the cwd-shift preamble landed too late (verified live 2026-05-31)."""
    tid = _ctx_thread(ctx)
    if not tid:
        return {"error": "no active thread"}
    title = (input_.get("title") or "").strip()
    if not title:
        return {"error": "title is required — name the analysis (e.g. the approved plan's title)"}
    from content.bio.lifecycle.runs import open_run
    rid = open_run(tid, title, focus_entity_id=(ctx or {}).get("focus_entity_id"))

    out: dict = {"status": "ok", "run_id": rid, "title": title}

    # Resolve the new cwd + prior files so the agent has all the absolute paths
    # it might need in its very next code cell.
    from core import projects
    project_id = projects.current() or "default"
    cwd_str = ""
    try:
        cwd_str = str(_run_scratch_cwd(str(project_id), str(tid)))
        out["cwd"] = cwd_str
    except Exception:  # noqa: BLE001
        pass
    preamble = _prior_run_files_preamble(str(project_id), tid, current_run_id=rid,
                                         cwd=cwd_str or None)

    note_parts = [
        "Run opened. Figures/tables you produce and the cells you execute now "
        "group under this Run until you close_run or open another.",
    ]
    if cwd_str:
        note_parts.append(
            f"YOUR cwd has just shifted to `{cwd_str}`. Bare relative paths from "
            f"earlier turns (`./geo_data/x.gz`, etc.) will NOT resolve here — "
            f"use the ABSOLUTE paths listed below to reach prior files."
        )
    out["note"] = " ".join(note_parts)
    if preamble:
        out["prior_files"] = preamble    # structured field for completeness
        out["note"] = out["note"] + "\n\n" + preamble
    return out


def close_run_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Close the thread's open Run (call when the user pivots to unrelated work)."""
    tid = _ctx_thread(ctx)
    if not tid:
        return {"error": "no active thread"}
    from content.bio.lifecycle.runs import close_run
    rid = close_run(tid)
    if not rid:
        return {"status": "noop", "note": "No open run to close."}
    return {"status": "ok", "closed_run_id": rid,
            "note": "Run closed. New outputs go to a fresh per-step analysis until you open_run again."}


def annotate_entity_tool(input_: dict, ctx: dict | None = None) -> dict:
    eid = input_.get("entity_id")
    if not eid:
        return {"error": "entity_id is required"}
    from core.graph.entities import get_entity, update_entity
    if not get_entity(eid):
        return {"error": f"entity {eid} not found"}
    fields = {k: input_[k] for k in ("tags", "notes", "title", "status")
              if k in input_ and input_[k] is not None}
    if not fields:
        return {"error": "nothing to update (pass tags/notes/title/status)"}
    update_entity(eid, **fields)
    return {"status": "ok", "entity_id": eid, "updated": list(fields.keys())}


def _archive_entity_tool(input_: dict, ctx: dict | None = None) -> dict:
    """archive_entity executor. Approval is handled by the framework BEFORE this
    runs (approval_policy='always' on the schema) — by the time we get here the
    user has confirmed via the UI modal."""
    from core.graph.entities import archive_entity, get_entity
    eid = (input_ or {}).get("entity_id") or ""
    if not eid:
        return {"status": "error", "note": "entity_id is required"}
    ent = get_entity(eid)
    if not ent:
        return {"status": "error", "note": f"entity {eid!r} not found"}
    if ent.get("status") == "archived":
        return {"status": "ok", "note": "entity was already archived",
                "entity_id": eid, "title": ent.get("title")}
    out = archive_entity(eid)
    if not out:
        return {"status": "error", "note": "archive failed (workspace cannot be archived)"}
    return {"status": "ok", "entity_id": eid, "title": out.get("title"),
            "note": f"Archived. Reversible via restore_entity (UI: Restore on the archived entity)."}
