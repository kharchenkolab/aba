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

import logging
_log = logging.getLogger(__name__)


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
    from core.data.refstore import available_scopes
    avail = available_scopes()
    shared = " (the shared store — reusable across all your projects)" if actual == "personal" else ""
    resp = {"status": "ok", "reference_id": eid, "sha": ident.get("sha"),
            "owned": owned, "scope": actual, "available_scopes": avail,
            "organism": d.get("organism"),
            "role": d.get("role"), "structural_path": d.get("structural_path"),
            "artifact_path": d.get("artifact_path"),
            "note": ("Owned content-addressed copy (deduplicated)." if owned
                     else "Linked in place — no copy.") + f" Registered at {actual!r} scope"
                     + shared + ". Reuse via find_reference."}
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
    from core.data.refstore import available_scopes
    try:
        res = promote_reference(ref_id, to)
    except Exception as e:  # noqa: BLE001
        return {"error": f"promote failed: {e}"}
    res.setdefault("available_scopes", available_scopes())
    # status reflects what ACTUALLY happened — a no-op is NOT "ok", so the agent
    # reports it honestly instead of claiming a move that didn't occur.
    return {"status": ("ok" if res.get("moved") else "noop"), **res}


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

    # Local asset (kind: local) → adopt the pre-existing on-cluster file in
    # place via a link (no download, no copy).
    if asset.get("path"):
        from pathlib import Path as _Path
        if not _Path(asset["path"]).exists():
            return {"error": f"local provider path does not exist: {asset['path']}",
                    "path": asset["path"]}
        spec = {"mode": "local", "provider": provider, "path": asset["path"],
                "version": asset.get("version")}
        try:
            eid = register_reference(
                asset["path"], organism=asset.get("organism") or input_.get("organism"),
                assembly=asset.get("assembly") or input_.get("assembly"),
                role=asset.get("role") or input_.get("role"),
                source=provider, version=asset.get("version"), acquisition=spec,
                mode="link", scope=input_.get("scope") or "group")
        except Exception as e:  # noqa: BLE001
            return {"error": f"register failed: {e}"}
        d = get_reference(eid) or {}
        return {"status": "ok", "reference_id": eid, "owned": d.get("owned"),
                "structural_path": d.get("structural_path"), "source": provider,
                "note": "Adopted pre-existing on-cluster reference in place (linked, no copy)."}

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
    find files the agent wrote there with a bare name.

    ORDER IS THE CONTRACT. `_resolve_dataset_path` takes the first candidate
    that exists, so this list decides which bytes a bare name means. Every
    local kernel is offered (a legitimate register can name a file written in
    an earlier kernel of the same run), but the CALLER'S kernel goes first and
    the rest sort newest-first.

    Unordered, this silently adopted another session's data: an agent
    downloaded a set of files, verified every one of them, and registered the
    directory by relative name — and got a partial download of the same name
    left in a kernel sandbox from two days earlier, because the store happened
    to list that kernel first. The entity went active carrying the agent's
    verification claim, which was true of the copy it made and false of the
    copy adopted. A stale sandbox must never outrank the caller's own.
    """
    bases: list[str] = []
    mine: list[str] = []
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
                ent = get_entity(rid) or {}
                ap = ent.get("artifact_path")
                if ap:
                    bases.append(str(ap))
                # the kernels THIS run executed in (run_exec records each one
                # via record_weft_target) — the caller's own sandboxes
                mine = [str(t) for t in
                        ((ent.get("metadata") or {}).get("weft_targets") or []) if t]
            bases.append(str(scratch_dir(pid, f"thread-{tid}")))
    except Exception:  # noqa: BLE001
        pass
    # Under weft, the kernel's cwd IS its ephemeral jobdir
    # ($ABA_HOME/weft/site-local/kernels/<kid>/) — the datasets2.md §1 bug:
    # a bare filename the agent just wrote resolves nowhere without these.
    try:
        import os as _os
        from core.compute.adapter import get_compute, weft_workspace
        _ws = weft_workspace()
        jds = [k.get("jobdir") for k in
               (get_compute().sync_call("list_kernels") or {}).get("kernels", [])
               if k.get("jobdir") and k.get("site") == "local"]

        def _rank(jd: str) -> tuple:
            # caller's kernels first (most recently recorded wins), then
            # everything else newest-first. mtime is the only defensible
            # tiebreak: the agent's own write is the newest thing on disk.
            try:
                owned = mine.index(jd)
                own_key = -(len(mine) - owned)      # last recorded = smallest
            except ValueError:
                own_key = 1                          # not ours → after all ours
            try:
                mt = _os.path.getmtime(_ws / "site-local" / jd)
            except OSError:
                mt = 0.0
            return (own_key, -mt)

        for jd in sorted(jds, key=_rank):
            bases.append(str(_ws / "site-local" / jd))
    except Exception:  # noqa: BLE001 — substrate offline → no jobdir candidates
        pass
    return bases


_CORRUPT_SCAN_CAP = 64          # files; a big tree pays a bounded price


def _corrupt_members(path: str) -> list[str]:
    """Names of compressed members under `path` that do NOT decompress.

    Cheap and decisive: the gzip/zip container carries its own end-of-stream
    marker, so a truncated or damaged download is detectable without knowing
    anything about the payload. Only reads members it can identify; anything
    else (plain text, binary, unknown suffix) is left alone — this answers
    "is the container intact", not "is the science right".
    """
    import gzip as _gz
    import os as _os
    import zipfile as _zf
    out: list[str] = []
    files = ([path] if _os.path.isfile(path)
             else [_os.path.join(r, f)
                   for r, _d, fs in _os.walk(path) for f in fs])
    for p in files[:_CORRUPT_SCAN_CAP]:
        low = p.lower()
        try:
            if low.endswith((".gz", ".bgz", ".tgz")):
                with _gz.open(p, "rb") as fh:      # reads to the EOS marker
                    while fh.read(1 << 20):
                        pass
            elif low.endswith(".zip"):
                with _zf.ZipFile(p) as z:
                    if z.testzip() is not None:
                        out.append(_os.path.basename(p))
        except Exception:  # noqa: BLE001 — unreadable IS the finding
            out.append(_os.path.basename(p))
    return out


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
    from core.config import project_data_dir
    from core.projects import current_project_id
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


def _resolve_dataset_path(path: str, ctx: dict | None,
                          _remote_out: "dict | None" = None) -> str:
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
    from core.config import project_data_dir
    from core.projects import current_project_id
    if os.path.isabs(path):
        return os.path.normpath(path)        # also collapses `./` segments
    # Canonical resolver FIRST (misc/paths.md P1): the Run's recorded outputs
    # — site-aware, stopped-kernel-aware — outrank any filesystem scan; the
    # ranked scratch scan (52c6d094) survives as the fallback and the only
    # tier for no-run registrations (uploads).
    try:
        from content.bio.lifecycle.runs import active_run_id, locate_run_output
        _tid = str((ctx or {}).get("thread_id") or "")
        _rid = active_run_id(_tid) if _tid else None
        if _rid:
            _hit = locate_run_output(_rid, path)
            if _hit and _hit.get("local_path") and os.path.exists(_hit["local_path"]):
                return os.path.normpath(_hit["local_path"])
            if _hit and _hit.get("locality") == "remote":
                # A remote hit is an ANSWER, not a miss (its local_path is
                # None by the lookup-never-transfers contract). Bytes move
                # only through the ONE mover, under the same small
                # transparent gate the serve surfaces use; a refusal is
                # stashed so the error can name the site instead of
                # advising an absolute path that cannot work across sites.
                try:
                    from content.bio.lifecycle.runs import materialize_run_output
                    from core.exec.run import _MAX_HARVEST_BYTES
                    _local = materialize_run_output(
                        _hit, max_bytes=_MAX_HARVEST_BYTES)
                    if _local and os.path.exists(_local):
                        return os.path.normpath(_local)
                except Exception as _me:  # noqa: BLE001 — refusal/size gate
                    if _remote_out is not None:
                        _remote_out.update(site=_hit.get("site"),
                                           size=_hit.get("size"),
                                           why=str(_me)[:200])
                if _remote_out is not None and "site" not in _remote_out:
                    _remote_out.update(site=_hit.get("site"),
                                       size=_hit.get("size"))
    except Exception:  # noqa: BLE001 — resolution falls to the ranked scan
        pass
    cands = [os.path.normpath(os.path.join(b, path)) for b in _scratch_bases(ctx)]
    cands.append(os.path.normpath(os.path.join(str(project_data_dir(current_project_id())), path)))
    cands.append(os.path.normpath(os.path.join(str(DATA_DIR), path)))
    cands.append(os.path.normpath(os.path.abspath(path)))
    hit = next((c for c in cands if os.path.exists(c)), None)
    if hit:
        return hit
    # Door tier: the name may live in a sandbox or a prior run's recorded
    # outputs (including the serving copy of something the agent just wrote on
    # a remote kernel). Local hits only — registration needs bytes on disk.
    try:
        from content.bio.project_locate import locate_project_files
        found = locate_project_files(os.path.basename(path), limit=4, ctx=ctx)
        local = [h for h in found.get("matches", []) if h.get("path")]
        if local:
            return os.path.normpath(local[0]["path"])
    except Exception:  # noqa: BLE001 — a fallback tier must never break resolution
        pass
    return cands[0]


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
    from core.config import project_data_dir
    from core.projects import current_project_id
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


def _producing_exec_id(input_: dict, ctx: dict | None) -> str | None:
    """The exec record to attach to a registered dataset so provenance shows the fetch
    code + env (misc/provenance.md). Explicit `exec_id` (from the run_python that
    produced the data) wins; else the most recent run in this thread — the fetch/download
    that just executed. None when neither is available."""
    explicit = (input_.get("exec_id") or "").strip()
    if explicit:
        return explicit
    try:
        from core.graph.exec_records import latest_exec_id_for_thread
        return latest_exec_id_for_thread(_ctx_thread(ctx))
    except Exception:  # noqa: BLE001
        return None


def _url_preflight(url: str) -> str | None:
    """Best-effort semantic pre-check for the URL lane — datasets.py's
    register_source explicitly leaves this to the caller. Catches the classic
    trap: `url=` pointing at a directory-listing / landing / error page, which
    would otherwise register a tiny HTML page as the "dataset" with no
    complaint. Controller-side HEAD; every INCONCLUSIVE outcome (no network
    here, server refuses HEAD, presigned URLs that 403 on HEAD) returns None —
    the real fetch may run on a site with different connectivity, so only a
    POSITIVE "this is HTML" / "this URL errors" blocks."""
    if not url.startswith(("http://", "https://")):
        return None                    # object-store schemes: no HEAD semantics
    from urllib.parse import urlsplit
    from urllib import request as _rq, error as _er
    if urlsplit(url).path.lower().endswith((".html", ".htm", ".xhtml")):
        return None                    # explicitly asking for an HTML document
    try:
        req = _rq.Request(url, method="HEAD",
                          headers={"User-Agent": "aba-dataset-preflight"})
        with _rq.urlopen(req, timeout=8) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except _er.HTTPError as e:
        if e.code in (403, 405, 501):  # HEAD refused/forbidden — inconclusive
            return None
        return (f"URL preflight: {url} answered HTTP {e.code} — nothing "
                f"fetchable there. Check the link (fetching anyway would have "
                f"registered the error page as the dataset).")
    except Exception:  # noqa: BLE001 — no network HERE ≠ no network on the site
        return None
    if ctype in ("text/html", "application/xhtml+xml"):
        return (f"URL preflight: {url} serves an HTML page ({ctype}), not a "
                f"data file — usually a directory listing or landing page; "
                f"registering it would mint a junk dataset. Pass a direct "
                f"file URL. For a multi-file bundle, fetch the files into one "
                f"directory (on the target site for remote data) and register "
                f"that directory with `path=` (+ `site=`).")
    return None


def _register_dataset_url(url: str, site: str | None, title: str,
                          input_: dict, ctx: dict | None,
                          _okind: 'str | None' = None) -> dict:
    """URL lane (misc/datasets2.md §4A): weft fetches into the target site's
    CAS, hashed on arrival — the agent never pre-downloads. With site=, the
    bytes land on THAT site and never touch the controller; locally we also
    stage a browsable copy into DATA_DIR (0-cost hardlink from the CAS)."""
    import os
    from core.config import project_data_dir
    from core.projects import current_project_id
    from core.data import datasets as _wds
    from core.graph.entities import create_entity, update_entity
    from core.graph.derivation import imported
    from content.bio.lifecycle.runs import agent_actor_for_thread
    err = _url_preflight(url)
    if err:
        return {"error": err}
    try:
        rec = _wds.register_source(url, site=site)
    except Exception as e:  # noqa: BLE001 — structured cause to the agent
        return {"error": f"could not fetch {url}: {e}"}
    abspath = None
    if not site or site == "local":
        try:
            slug = _slugify_for_dir(title)
            dest = str(project_data_dir(current_project_id()) / slug)
            _get_compute_sync("data_fetch", rec["ref"], dest)
            abspath = dest
        except Exception:  # noqa: BLE001 — CAS copy still holds the bytes
            pass
    md = {"thread_id": _ctx_thread(ctx), "origin": "url",
          "origin_kind": _okind or "url",
          "by_reference": abspath is None, "ref_path": abspath or url,
          "summary": (input_.get("summary") or "").strip(),
          "source": input_.get("source") or url,
          "organism": input_.get("organism"),
          "source_key": rec["source_key"], "ref": rec["ref"],
          "origin_class": rec["origin_class"],
          "descriptor": rec.get("descriptor") or {},
          "dataset_site": site or "local"}
    eid = create_entity(entity_type="dataset", title=title,
                        artifact_path=abspath,
                        derivation=imported(url),
                        actor=agent_actor_for_thread(_ctx_thread(ctx)),
                        exec_id=_producing_exec_id(input_, ctx),
                        metadata=md)
    if md["summary"]:
        update_entity(eid, notes=md["summary"])
    where = (f"fetched onto site {site!r} (bytes never touched this machine)"
             if site and site != "local" else
             f"fetched and available at {abspath}" if abspath else
             "fetched into the local data store")
    return {"status": "ok", "dataset_id": eid, "title": title,
            "artifact_path": abspath,
            "provenance": _okind or "url",
            "note": f"Registered as a Dataset entity — {where}."}


def _get_compute_sync(name: str, *a, **kw):
    from core.compute.adapter import get_compute
    return get_compute().sync_call(name, *a, **kw)


def _slugify_for_dir(title: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(title)).strip("-")
    return (s or "dataset")[:80]


def _weft_adopt(abspath: str, title: str,
                _thread_id: 'str | None' = None) -> tuple[str, dict]:
    """Weft-native adopt for PRODUCED/fetched bytes (replaces the plain
    copy-into-data-dir): CAS ingest mints the content identity (dedup — the
    same content re-registered is the same ref, re-fetches stop piling up),
    then a 0-cost local data_fetch gives DATA_DIR its browsable view.
    Returns (data_dir_path, weft_metadata)."""
    import os
    from core.config import project_data_dir
    from core.projects import current_project_id
    from core.data import datasets as _wds
    rec = _wds.ingest_produced(abspath)
    base = os.path.basename(abspath.rstrip("/")) or _slugify_for_dir(title)
    dest = str(project_data_dir(current_project_id()) / base)
    if os.path.abspath(dest) != os.path.abspath(abspath):
        _get_compute_sync("data_fetch", rec["ref"], dest)
    md = {"ref": rec["ref"], "origin_class": rec["origin_class"],
          "source_key": rec["source_key"],
          "descriptor": rec.get("descriptor") or {}}
    _capture_run_key(abspath, md, _thread_id)
    return dest, md


def _capture_run_key(abspath: str, md: dict,
                     _thread_id: "str | None" = None) -> None:
    """Record the durable (run, rel) KEY for bytes born in a kernel jobdir —
    the handle that survives sweeps, keeps and PLACE moves (paths are
    lookups, never identities). Canonical resolver FIRST (misc/paths.md P2):
    its tiers are site-aware, so a file born on a site-targeted kernel still
    gets its handle; the local prefix scan below cannot see those and stays
    only as the no-run fallback (census-allowlisted)."""
    try:
        from core.compute.adapter import get_compute, weft_workspace
        real = os.path.realpath(abspath)
        try:
            from content.bio.lifecycle.runs import (active_run_id,
                                                    locate_run_output,
                                                    record_weft_target)
            _rid = active_run_id(_thread_id) if _thread_id else None
            if _rid is None and _thread_id:
                # F11 parity (keep_outputs already does this): a quick/no-plan
                # thread has no Run yet, so kernel-start target recording
                # no-op'd — resolve-or-create the AMBIENT run and backfill
                # this thread's kernel targets, or a remote-born file loses
                # its durable (run, rel) linkage forever (live: the remote
                # wing's F3 red, link 1).
                try:
                    from content.bio.lifecycle.registry import _ensure_analysis
                    _rid = _ensure_analysis(None, {}, _thread_id)
                except Exception:  # noqa: BLE001
                    _rid = None
            if _rid:
                try:
                    from core.exec.kernels import get_pool
                    for _lang in ("python", "r"):
                        _sess = get_pool().peek(_thread_id, _lang)
                        if _sess is not None:
                            record_weft_target(_rid,
                                               getattr(_sess, "kernel_id", None))
                except Exception:  # noqa: BLE001
                    pass
                _hit = locate_run_output(_rid, os.path.basename(abspath))
                if _hit and _hit.get("target") and _hit.get("rel"):
                    md["run_key"] = {"run": _hit["target"], "rel": _hit["rel"]}
                    return
        except Exception:  # noqa: BLE001 — fall to the local scan
            pass
        for k in (get_compute().sync_call("list_kernels") or {})                 .get("kernels", []):
            jd = k.get("jobdir")
            if not jd or k.get("site") != "local":
                continue
            root = os.path.realpath(str(weft_workspace() / "site-local" / jd))
            if real.startswith(root + os.sep):
                md["run_key"] = {"run": k.get("kernel_id"),
                                 "rel": os.path.relpath(real, root)
                                 .replace(os.sep, "/")}
                break
    except Exception:  # noqa: BLE001 — the key is enrichment, never a gate
        pass


def _paths_set_source_key(paths_list, site) -> str | None:
    """Semantic identity of a multi-file registration: the sorted member
    paths, order-free. ONE function for the dedup check AND the stored
    metadata — the SET shape had no dedup at all (check-time key, nothing
    persisted), and the register-on-landing rule funnels agents onto exactly
    this shape, so a retried fetch or resumed session minted duplicate
    entities with no backstop."""
    if not paths_list or not all(os.path.isabs(str(x)) for x in paths_list):
        return None
    from core.data import datasets as _wds
    return _wds.source_key(
        "|".join(sorted(os.path.normpath(str(x)) for x in paths_list)), site)


_ORIGIN_KINDS = {"url", "upload", "derived", "collaborator",
                 "instrument", "simulated", "public_registry", "unknown"}


def register_dataset_tool(input_: dict, ctx: dict | None = None) -> dict:
    import os
    from core.config import DATA_DIR
    from core.config import WORK_DIR, project_data_dir, project_work_dir
    from core.projects import current_project_id
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
    # Agent-STATED origin (misc/paths.md follow-on): where the dataset is
    # from is conversation meaning — only the agent holds it, and it is
    # unrecoverable after registration. Structured kind + the existing
    # `source` as the traceable ref; absence is legal but LOUD (the result
    # carries provenance: unstated), never silent. The url door pre-fills
    # what the system genuinely knows; it never invents meaning.
    _okind = (str(input_.get("origin") or "").strip().lower() or None)
    if _okind is not None and _okind not in _ORIGIN_KINDS:
        return {"error": f"origin must be one of {sorted(_ORIGIN_KINDS)} "
                         f"(got {_okind!r}); use 'unknown' to state that "
                         f"the origin is genuinely not known"}
    if _okind is None and input_.get("url"):
        _okind = "url"
    paths_list = input_.get("paths")
    path = input_.get("path")
    url = (input_.get("url") or "").strip() or None
    site = (input_.get("site") or "").strip() or None
    given = [x for x in (paths_list, path, url) if x]
    if not given:
        return {"error": "one of `url`, `path` (single file/dir), or `paths` "
                         "(list of files) is required"}
    if len(given) > 1:
        return {"error": "pass exactly one of `url`, `path`, `paths`"}

    from core.graph.entities import create_entity, update_entity

    # Semantic dedup BEFORE obtaining/registering (misc/datasets2.md §4):
    # the same source registered twice is the same dataset — say so instead
    # of re-fetching / minting a duplicate entity.
    from core.data import datasets as _wds
    try:
        if url:
            _skey = _wds.source_key(url)
        elif path and os.path.isabs(str(path)):
            _skey = _wds.source_key(os.path.normpath(str(path)), site)
        elif paths_list:
            _skey = _paths_set_source_key(paths_list, site)
        else:
            _skey = None
        if _skey:
            from core.graph.entities import find_entities
            hits = find_entities(type="dataset", not_deleted=True,
                                 metadata_contains={"source_key": _skey})
            if hits:
                _h = hits[0]
                return {"status": "ok", "dataset_id": _h["id"],
                        "title": _h.get("title"), "already_registered": True,
                        "note": (f"This source is already registered as dataset "
                                 f"{_h['id']} ({_h.get('title')!r}) — reusing it. "
                                 "No bytes were fetched or copied.")}
    except Exception:  # noqa: BLE001 — dedup is best-effort, never blocks registration
        pass

    if url:
        return _register_dataset_url(url, site, title, input_, ctx,
                             _okind=_okind)

    bundle_note = ""
    weft_md = {}
    if paths_list:
        # Multi-path mode: resolve each, link them into a fresh DATA_DIR/<slug>/
        # bundle, register THAT bundle. Lets the agent register only the files
        # it actually wants without dragging derivatives that sit nearby on disk.
        # This is a LOCAL operation (hardlink/copy into this machine's project
        # data dir) — refuse a remote `site=` instead of silently dropping it:
        # the files live on the site, the links would all come up missing, and
        # the caller would get a hollow bundle with no hint why.
        if site and site != "local":
            return {"error": (
                f"`paths=[…]` bundles files into this machine's DATA_DIR and "
                f"cannot target a remote site (site={site!r} would be ignored). "
                f"For a multi-file bundle on {site!r}: put the files into ONE "
                f"directory there (e.g. a background job on the site), then "
                f"register that directory with `path=<dir>, site={site!r}` — "
                f"one dataset, bytes stay on the site.")}
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
        # Persist the SAME set identity the dedup check computes — a key that
        # is checked but never stored can never match a prior registration.
        _set_key = _paths_set_source_key(paths_list, site)
        if _set_key:
            weft_md["source_key"] = _set_key
    else:
        _remote_miss: dict = {}
        abspath = _resolve_dataset_path(str(path), ctx, _remote_out=_remote_miss)
        exists = os.path.exists(abspath)
        # Adopt: a file found in the SCRATCH tier (not already under DATA_DIR)
        # goes weft-native (misc/datasets2.md §4B): CAS ingest mints the
        # content identity (dedup, survives the kernel-jobdir sweep), then a
        # 0-cost hardlink fetch gives DATA_DIR its browsable view. Falls back
        # to the legacy plain adopt when the substrate is offline.
        adopted = materializing = False
        weft_md: dict = {}
        # "Is this file in scratch?" / "is it already in DATA_DIR?" must check
        # the PER-PROJECT trees the agent actually writes to, plus the module
        # ones for back-compat — and the weft workspace, because under weft
        # the kernel cwd is an ephemeral jobdir inside it.
        in_work = (_within(abspath, str(WORK_DIR)) or
                   _within(abspath, _PROJECT_WORK))
        try:
            from core.compute.adapter import weft_workspace as _wws
            in_work = in_work or _within(abspath, str(_wws()))
        except Exception:  # noqa: BLE001
            pass
        in_data = (_within(abspath, str(DATA_DIR)) or
                   _within(abspath, _PROJECT_DATA))
        if exists and in_work and not in_data:
            try:
                abspath, weft_md = _weft_adopt(abspath, str(title),
                               _thread_id=_ctx_thread(ctx))
                adopted = True
            except Exception:  # noqa: BLE001 — substrate offline → legacy adopt
                try:
                    abspath, materializing = _adopt_into_data_dir(abspath)
                    adopted = True
                except Exception:  # noqa: BLE001 — by-reference at the scratch path
                    pass
        elif exists and not in_data:
            # In place outside aba's trees: a DURABLE-HOME registration
            # (misc/datasets2.md §4C) — fingerprint + descriptor, NO ingest,
            # NO copy; the content identity (ref) mints lazily at first use.
            try:
                from core.data import datasets as _wds2
                rec = _wds2.register_source(abspath, site=site)
                weft_md = {k: rec[k] for k in
                           ("source_key", "home", "fingerprint", "descriptor",
                            "origin_class") if rec.get(k) is not None}
            except Exception:  # noqa: BLE001 — plain by-reference still works
                pass
        elif not exists and site and site != "local":
            # A path that lives on a REMOTE site (never visible locally):
            # register the durable home site-side — bytes never touch this
            # machine (the whole point for TB-scale remote data).
            try:
                from core.data import datasets as _wds2
                rec = _wds2.register_source(str(path), site=site)
                if (rec.get("fingerprint") or {}).get("exists"):
                    abspath = os.path.normpath(str(path))
                    exists = True   # exists ON THE SITE — honest enough for the entity
                    weft_md = {k: rec[k] for k in
                               ("source_key", "home", "fingerprint",
                                "descriptor", "origin_class")
                               if rec.get(k) is not None}
                else:
                    return {"error": (f"nothing found at {path!r} on site "
                                      f"{site!r} — check the path (it is "
                                      f"checked on that machine, not here)")}
            except Exception as e:  # noqa: BLE001
                return {"error": f"could not reach site {site!r} to check "
                                 f"{path!r}: {e}"}
        # Fix 3: a path that doesn't exist can't be registered (the dataset
        # schema requires artifact_path). Return a clear, actionable error
        # instead of letting create_entity raise the cryptic "required field
        # 'artifact_path' is missing or empty" — the symptom when a relative
        # path resolved against a now-gone per-run scratch dir.
        if not exists:
            # Branch on what the caller ALREADY supplied: telling someone who
            # passed an absolute path to "pass an ABSOLUTE path" names the one
            # thing they did — the input actually missing there is site=
            # (an absolute path is exists()-checked on THIS controller; on a
            # site-targeted kernel that check is about the wrong machine).
            if _remote_miss.get("site"):
                _sz = _remote_miss.get("size")
                return {"error": (
                    f"{path!r} was found on site {_remote_miss['site']!r}"
                    + (f" ({_sz} bytes)" if _sz else "")
                    + " but was not brought here"
                    + (f" ({_remote_miss['why']})" if _remote_miss.get("why")
                       else " (size gate)")
                    + f" — register it in place with path=<absolute path on "
                      f"the site> + site='{_remote_miss['site']}', or keep it "
                      f"as a run output (keep_outputs) and register later."
                )}
            if os.path.isabs(str(path)):
                return {"error": (
                    f"Nothing to register: {path!r} does not exist on this "
                    f"controller. If the file was written by a site-targeted "
                    f"kernel (run_python(site=…)), pass site= to "
                    f"register_dataset — an absolute path alone is checked "
                    f"locally and cannot reach another site's filesystem."
                )}
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
    # By-reference (external, not adopted) → capture a drift baseline so we can later flag when the
    # referenced payload changes or vanishes (misc/external_import.md). Stored INLINE so it lands in
    # the entity sidecar and survives a DB-crash recovery. Stat-only; skipped for adopted/copied
    # datasets (those are ABA-owned in DATA_DIR, so there's nothing to drift).
    _md = {"thread_id": _ctx_thread(ctx), "origin": "external",
           "origin_kind": _okind or "unstated",
           "by_reference": not adopted, "ref_path": abspath,
           "summary": summary, "source": input_.get("source", ""),
           "organism": input_.get("organism"), **weft_md}
    # evidence enrichment runs for EVERY registration, not only adopted
    # bytes — a by-reference file in a kernel jobdir deserves its durable
    # key too (the adopt-only capture was a door-shaped gap)
    if "run_key" not in _md and exists:
        _capture_run_key(abspath, _md, _ctx_thread(ctx))
    # cross-check: authored claim vs mechanical evidence — a dataset said to
    # be an upload/collaborator hand-off that carries a kernel run_key was
    # BORN here; flag the contradiction, never silently prefer either side
    if _okind in ("upload", "collaborator") and _md.get("run_key"):
        _md["origin_mismatch"] = ("stated origin is external-to-platform but "
                                  "the bytes carry a kernel run_key")
    _remote_home = ((weft_md.get("home") or {}).get("site") or "local") != "local"
    if exists and not adopted and not _remote_home:
        try:
            from core.data.external_ref import fingerprint as _fp
            _md["import_fingerprint"] = _fp(abspath)
        except Exception:  # noqa: BLE001 — a missing baseline just means no drift detection
            pass
    eid = create_entity(
        entity_type="dataset", title=title,
        artifact_path=abspath if exists else None,
        derivation=imported(input_.get("source") or "external"),   # Phase 2B
        actor=agent_actor_for_thread(_ctx_thread(ctx)),            # Phase 2B
        # Link the producing run's exec record (the fetch/download) so the dataset's
        # provenance shows the code + env + source, not just "imported" (provenance.md).
        exec_id=_producing_exec_id(input_, ctx),
        metadata=_md)
    if summary:
        # The dataset detail view shows `notes` as the description — populate it.
        update_entity(eid, notes=summary)
    note = "Registered as a Dataset entity — now in the Data facet."
    if _remote_home:
        note += (f" The data stays on {(weft_md.get('home') or {}).get('site')} "
                 f"at {abspath} — read in place, never copied to this machine.")
    if adopted and not materializing:
        note += " Files adopted into DATA_DIR (kept past scratch cleanup)."
    elif adopted and materializing:
        note += " Files are copying into DATA_DIR in the background — fully available shortly."
    # Integrity at the boundary: whatever the resolver bound to, the bytes that
    # landed are what the project will read. A compressed member that will not
    # decompress is a BROKEN dataset, and saying so here costs milliseconds —
    # while NOT saying it cost two agents four minutes and eight tool calls
    # diagnosing damaged data that was intact at its source. Advisory, never
    # fatal: the entity still registers (the caller may want to repair in
    # place), but the result never reads as a clean success.
    _bad = _corrupt_members(abspath) if (exists and not _remote_home) else []
    if _bad:
        note += (f" WARNING: {len(_bad)} file(s) in this dataset do not "
                 f"decompress — {', '.join(_bad[:3])}"
                 f"{' …' if len(_bad) > 3 else ''}. The registered copy is "
                 f"damaged or partial; re-fetch before using it (verifying a "
                 f"file where you downloaded it does NOT mean the registered "
                 f"copy is intact).")
    if bundle_note:
        note += bundle_note
    if not exists:
        note += " WARNING: path not found on disk; registered by reference only — pass a path under DATA_DIR."
    # S3 (2026-06-02): include a one-line layout hint + the canonical path back
    # in the tool_result so the agent's next turn has the location in
    # conversation context. Removes the "I just registered it — now where does
    # it live?" round-trip that was costing 3-5 tool calls (prj_8d699668).
    layout_hint = _dataset_layout_hint(abspath) if exists and not _remote_home else ""
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
    if not _okind:
        note += " PROVENANCE UNSTATED: say where this dataset is from — re-register or note it now (origin= kind + source= traceable ref); this is unrecoverable later."
    elif _md.get("origin_mismatch"):
        note += (f" NOTE: {_md['origin_mismatch']} — double-check the stated "
                 f"origin.")
    return {"status": "ok", "dataset_id": eid, "title": title,
            "artifact_path": abspath if exists else None,
            "layout_hint": layout_hint or None,
            "provenance": _okind or "unstated",
            "note": note}


def check_import_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Check whether an IMPORTED (by-reference) Run or Dataset still matches the external directory
    it references — flag-only drift, using the baseline captured at import (misc/external_import.md).
    Fast (a stat-walk against the stored fingerprint), never re-copies. This is how the agent
    'maintains' an imported entity: answer 'is it still current?' and, if stale, guide a refresh."""
    from core.graph.entities import get_entity
    from core.data.external_ref import check_drift
    eid = (input_.get("entity_id") or "").strip()
    if not eid:
        return {"error": "check_import needs `entity_id`."}
    ent = get_entity(eid)
    if not ent:
        return {"error": f"no entity {eid!r}"}
    md = ent.get("metadata") or {}
    ref = md.get("ref_path")
    # weft durable-home datasets (misc/datasets2.md): the fingerprint was taken
    # SITE-SIDE — revalidate there too (works for remote homes the controller
    # can't stat), and mark drift on the entity for the pre-submit fence.
    if md.get("home"):
        from core.data import datasets as _wds
        v = _wds.revalidate(md)
        home = md["home"]
        if v["state"] == "unchanged":
            return {"entity_id": eid, "by_reference": True, "stale": False,
                    "ref_path": home.get("path"),
                    "note": f"Up to date — {home.get('path')} on "
                            f"{home.get('site')} still matches the "
                            "registration fingerprint."}
        reason = "missing" if v["state"] == "missing" else "changed"
        return {"entity_id": eid, "by_reference": True, "stale": True,
                "reason": reason, "ref_path": home.get("path"),
                "note": (f"STALE ({reason}): the data at {home.get('path')} on "
                         f"{home.get('site')} "
                         + ("is gone or unreachable."
                            if reason == "missing" else
                            "has changed since registration (size/mtime "
                            "mismatch).")
                         + " Re-register it (a new revision) before using it "
                           "in new analyses — results memoized against the "
                           "old content will not be reused.")}
    if not md.get("by_reference"):
        return {"entity_id": eid, "by_reference": False, "stale": False,
                "note": "Not imported by reference — its data is ABA-owned, so there's nothing to "
                        "drift-check."}
    d = check_drift(md)
    if not d.get("stale"):
        return {"entity_id": eid, "by_reference": True, "stale": False, "ref_path": ref,
                "note": f"Up to date — the external payload at {ref} still matches the import baseline."}
    if d.get("reason") == "missing":
        return {"entity_id": eid, "by_reference": True, "stale": True, "reason": "missing",
                "ref_path": ref,
                "note": (f"STALE (missing): {ref} is gone or unreadable. The imported "
                         f"{ent.get('type')} record is intact in ABA, but its external payload can't "
                         f"be reached. Ask the user for the new location, then re-run import_run.")}
    return {"entity_id": eid, "by_reference": True, "stale": True, "reason": "changed",
            "ref_path": ref, "detail": d.get("detail"),
            "note": (f"STALE (changed): the external results at {ref} differ from the import baseline "
                     f"({d.get('detail')}). The collaborator likely re-ran it — re-run import_run on "
                     f"that same path to refresh this run with the current outputs.")}


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
    # A remote-home dataset's directory lives on its site; a local isdir()
    # check would fail and mislabel it "not directory-shaped". Refuse with
    # the real reason and the working recipe instead.
    md = ent.get("metadata") or {}
    home_site = ((md.get("home") or {}).get("site")
                 or md.get("dataset_site") or "local")
    if home_site != "local":
        where = ent.get("artifact_path") or md.get("ref_path")
        return {"error": (
            f"dataset {dsid} lives on site {home_site!r} at {where!r} — this "
            f"tool links files locally and cannot reach it. Write the new "
            f"files into that directory ON {home_site!r} (e.g. a background "
            f"job on the site); they become part of the dataset in place.")}
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
        # actionable path, not just a verdict (observed live: handed an
        # exec id, the agent got "not found" and wandered through
        # list_entities/pin_cell guesses for a full turn)
        return {"error": str(e),
                "hint": "figures become entities only when PINNED — pin the "
                        "producing cell first (pin_cell), then promote the "
                        "resulting FIGURE id (fig_…); exec/cell ids can't be "
                        "promoted directly"}
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
    # §8f: a re-run WITH CHANGES branches — record the baseline as scenario_of,
    # but only if it names a real analysis Run (never self, never a bad id).
    rerun_of = (input_.get("rerun_of") or "").strip() or None
    if rerun_of:
        from core.graph.entities import get_entity as _ge
        base = _ge(rerun_of)
        if not base or base.get("type") != "analysis":
            rerun_of = None
    from content.bio.lifecycle.runs import open_run
    rid = open_run(tid, title, focus_entity_id=(ctx or {}).get("focus_entity_id"),
                   scenario_of=rerun_of)

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


def keep_outputs_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Level-2 keep triage for the current Run's outputs (output_durability.md §6.1).

    Obvious keepers (surfaced figures/tables, declared finals) and obvious scratch (tmp/,
    cache/, *.tmp, chunk_*) are handled automatically — you only need this for the AMBIGUOUS
    set: name a large intermediate to DROP so it isn't kept, or a file the folder heuristic
    would drop that you want to KEEP. Paths/globs are relative to the Run's output dir."""
    from content.bio.lifecycle.runs import active_run_id, set_keep_decision
    rid = (input_.get("run_id") or "").strip() or None
    tid = _ctx_thread(ctx) if not rid else None
    if not rid and tid:
        rid = active_run_id(tid)
    if not rid and tid:
        # F11: a "keep this for the project" in a quick / no-plan flow, where the
        # user never opened a Run, previously hard-errored here — so the keep
        # silently no-op'd and the file was never durably kept. Lazily
        # resolve-or-create the thread's AMBIENT Run via the SAME mechanism the
        # artifact-registration hook uses (registry._ensure_analysis): its
        # artifact_path IS the thread scratch dir where run_python just wrote
        # these files, so the keep attaches to the Run that actually holds them.
        try:
            from content.bio.lifecycle.registry import _ensure_analysis
            rid = _ensure_analysis((ctx or {}).get("focus_entity_id"), {}, tid)
        except Exception as e:  # noqa: BLE001 — fall through to the honest error
            _log.warning("keep_outputs: ambient-run resolution failed: %s", e)
    if not rid:
        return {"error": "no run to keep into — pass run_id, or run a step in "
                         "this thread first so its outputs can be kept"}
    keep = input_.get("keep") or []
    drop = input_.get("drop") or []
    if isinstance(keep, str):
        keep = [keep]
    if isinstance(drop, str):
        drop = [drop]
    out = set_keep_decision(rid, keep=keep, drop=drop)
    if out.get("error"):
        return out
    s = out.get("summary") or {}
    note = (f"Keep decision applied. retained={s.get('retained', 0)} saving={s.get('saving', 0)} "
            f"at_risk={s.get('at_risk', 0)}. Excluded won't be retained at run-close either.")
    # Honesty guard (found live: a keep naming an unharvested file silently
    # covered 1 of 2 — the user was told "kept" while the file sat outside
    # the tracked inventory). A LITERAL include that matches nothing in the
    # run's tracked outputs is surfaced, never swallowed.
    try:
        from core.exec.artifacts import artifacts_for_run
        tracked = {(a.get("original_name") or "").strip()
                   for a in artifacts_for_run(rid)}
        # F10 disk truth: a literal include that EXISTS on disk (local sandbox
        # or a target's inventory) IS covered — the keeper set carries it and
        # the retain matches it in place, tracked-or-not. Only a literal that
        # is in NEITHER the tracked outputs NOR the real listing is unmatched.
        disk_seen = set(out.get("disk_seen") or [])
        unmatched = [p.strip() for p in keep
                     if p and p.strip() and not any(c in p for c in "*?[")
                     and p.strip() not in tracked
                     and p.strip() not in disk_seen]
        if unmatched:
            out["unmatched_includes"] = unmatched
            note += (" NOT COVERED: " + ", ".join(unmatched) +
                     " — found neither in the run's tracked outputs nor on "
                     "disk in its working area, so this keep does NOT protect "
                     "them. Check the path; tell the user; do not describe "
                     "these files as kept.")
        for g in (out.get("size_gated") or []):
            note += (f" SIZE GATE: '{g['glob']}' matched {g['files']} "
                     f"untracked file(s) totaling {g['bytes'] / 1e9:.1f} GB — "
                     f"NOT auto-kept (too large for a silent commitment). "
                     f"Name specific files to keep them, or confirm with the "
                     f"user and keep the largest ones explicitly.")
        if out.get("disk_kept"):
            note += (" Also kept (disk-truth glob matches outside tracked "
                     "outputs): " + ", ".join(out["disk_kept"][:6])
                     + ("…" if len(out["disk_kept"]) > 6 else "") + ".")
    except Exception:  # noqa: BLE001 — the guard must never break the keep
        pass
    return {"status": "ok", **out, "note": note}


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
