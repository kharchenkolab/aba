"""Nested files-tree composer (files.md §3.3).

Walks the entity graph to compose the bio file tree:
  threads → runs/results/claims, runs → child files, results → member
  files. Same canonical artifact may appear at multiple paths
  (symlink-style). Generated README placeholders at every container level.

This module is bio because the hierarchy itself is bio-shaped (what
counts as a "thread", which entity types nest under what, which edges
mean "produced by"). The platform primitives it uses
(core.files.registry's slug + ext helpers, core.graph.edges) are
domain-neutral.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Optional

from core.config import ARTIFACTS_DIR
from core.files.registry import slugify, ext_from_artifact, name_with_ext
from core.graph.entities import list_entities
from core.graph.edges import edges_to


# Per-bio-type categorization driven by the entity-type registry
# (Phase 4.3 Pass 2). Each YAML declares `category: <name>`; the sets
# below are derived once at module load. Adding a new bio type with
# `category: leaf` automatically picks it up here without code change.
from core.entity_types import types_in_category as _types_in_category
LEAF_TYPES    = _types_in_category("leaf")     # figure, table, dataset, note, narrative
CLAIM_TYPES   = _types_in_category("claim")    # claim
RESULT_TYPES  = _types_in_category("result")   # result
RUN_TYPES     = _types_in_category("run")      # analysis
THREAD_TYPES  = _types_in_category("thread")   # thread
FINDING_TYPES = _types_in_category("finding")  # finding
PLAN_TYPES    = _types_in_category("plan")     # plan

# Where a leaf lives under a run by default (subdir name → entity type).
RUN_SUBDIRS = {"figure": "figures", "table": "tables"}

# Synthesized-text extensions.
PROSE_EXTS = {"note": ".md", "narrative": ".md", "claim": ".md"}


def _resolve_disk(artifact_path: Optional[str]) -> Optional[Path]:
    if not artifact_path:
        return None
    if artifact_path.startswith("/artifacts/"):
        parts = artifact_path[len("/artifacts/"):].split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            # New per-project shape: /artifacts/<pid>/<name>
            from core.config import project_artifacts_dir
            return project_artifacts_dir(parts[0]) / parts[1]
        if len(parts) == 1:
            # Legacy workspace-level: /artifacts/<name>
            return ARTIFACTS_DIR / parts[0]
        return None
    return Path(artifact_path)


def _file_meta(e: dict) -> dict:
    """size + mtime if the on-disk artifact exists, else (None, None)."""
    disk = _resolve_disk(e.get("artifact_path"))
    size = mtime = None
    if disk and disk.exists():
        try:
            st = disk.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError:
            pass
    return {"size": size, "mtime": mtime, "disk_path": str(disk) if disk else None}


def _dataset_home_site(e: dict) -> Optional[str]:
    """The site a dataset's bytes live on, from `metadata.home.site`; None if unset."""
    return ((e.get("metadata") or {}).get("home") or {}).get("site")


def _dataset_descriptor(e: dict) -> dict:
    """`{top, n_files, total_bytes}` for a dataset — from `metadata.descriptor`,
    falling back to `metadata.fingerprint`. `top` is the real first-level names the
    controller captured even when it never holds the bytes."""
    md = e.get("metadata") or {}
    desc = md.get("descriptor") or {}
    fp = md.get("fingerprint") or {}
    top = desc.get("top") or fp.get("top") or []
    n_files = desc.get("n_files") or fp.get("n_files") or len(top)
    total_bytes = desc.get("total_bytes") or fp.get("total_bytes")
    return {"top": list(top), "n_files": n_files or 0, "total_bytes": total_bytes}


def _is_remote_dataset(e: dict) -> bool:
    """A by-reference / non-local-home dataset — its bytes are on another site, so
    the controller's local disk walk must not run (it would fabricate a `.bin`)."""
    md = e.get("metadata") or {}
    site = _dataset_home_site(e)
    return bool(md.get("by_reference")) or bool(site and site != "local")


def _is_remote_dir_dataset(e: dict) -> bool:
    """A remote dataset shaped as a directory (more than one real file) — the case
    `_leaf_name` must not coerce into a single `.bin`."""
    return _is_remote_dataset(e) and (_dataset_descriptor(e)["n_files"] or 0) > 1


def _remote_dataset_node(e: dict, seg: str) -> dict:
    """Build a dataset node for a remote/by-reference home from the captured
    descriptor — NEVER the controller's local disk (which holds no bytes). A
    directory home (n_files > 1) becomes an entity-backed folder with one child per
    real filename (sizes unknown → null, not a lie); a single-file home stays a
    leaf. Each node carries `site` so the UI can label the home."""
    site = _dataset_home_site(e)
    desc = _dataset_descriptor(e)
    top, n_files = desc["top"], desc["n_files"]
    ap = (e.get("artifact_path") or "").rstrip("/")
    if n_files > 1 or len(top) > 1:
        ds = _folder(seg, path=f"datasets/{seg}", entity=e)
        ds["site"] = site
        for nm in top:
            ds["children"].append({
                "kind": "file",
                "name": nm,
                "path": f"datasets/{seg}/{nm}",
                "entity_id": None,
                "entity_type": None,
                "title": nm,
                "artifact_path": (f"{ap}/{nm}" if ap else None),
                "size": None,      # per-file size isn't captured; total_bytes is aggregate
                "mtime": None,
                "site": site,
            })
        return ds
    node = _file_node(e, path=f"datasets/{_leaf_name(e)}")
    node["site"] = site
    return node


def _leaf_name(e: dict) -> str:
    """Filename for a leaf entity. Uses title slug + extension lookup;
    name_with_ext skips an already-present suffix so a title like
    'sample_cells_15.csv' doesn't become 'sample_cells_15.csv.csv'."""
    slug = slugify(e.get("title") or e.get("id") or "untitled")
    t = e.get("type") or ""
    if t in PROSE_EXTS:
        return name_with_ext(slug, PROSE_EXTS[t])
    ext = ext_from_artifact(e, default=".bin")
    # Guard: a remote/by-reference DIRECTORY home has a dot-less final segment
    # (e.g. `.../GSM5746259`), which would otherwise coerce into a fabricated
    # single ".bin" leaf. Such a dataset is rendered from its descriptor (see the
    # datasets branch below); never invent an extension for it here.
    if ext == ".bin" and _is_remote_dir_dataset(e):
        ext = ""
    return name_with_ext(slug, ext)


def _folder_slug(e: dict) -> str:
    return slugify(e.get("title") or e.get("id") or "container")


# ---------- Node constructors ----------

def _file_node(e: dict, *, path: str) -> dict:
    fm = _file_meta(e)
    return {
        "kind": "file",
        "name": _leaf_name(e),
        "path": path,
        "entity_id": e["id"],
        "entity_type": e["type"],
        "title": e.get("title"),
        "artifact_path": e.get("artifact_path"),
        "size": fm["size"],
        "mtime": fm["mtime"],
        "pinned": bool(e.get("pinned")),
        "status": e.get("status"),
    }


def _prose_node(e: dict, *, path: str) -> dict:
    """Text-only entities (claim, narrative, note) materialize as a .md."""
    return {
        "kind": "file",
        "name": _leaf_name(e),
        "path": path,
        "entity_id": e["id"],
        "entity_type": e["type"],
        "title": e.get("title"),
        "artifact_path": None,
        "size": None,
        "mtime": _entity_mtime(e),
        "synthesized": True,
        "status": e.get("status"),
    }


def _plan_markdown(plan_entity: dict) -> str:
    """Render a plan entity's structured body as .md prose. Mirrors
    _claim_markdown's role: produces durable, browsable text that a user
    (or future agent) can read independently of the SSE event log."""
    meta = plan_entity.get("metadata") or {}
    plan = meta.get("plan") or {}
    lifecycle = meta.get("plan_lifecycle") or "validated"
    lines = [f"# {plan.get('title') or plan_entity.get('title') or 'Plan'}", ""]
    lines.append(f"**Lifecycle:** {lifecycle}")
    lines.append("")
    if plan.get("summary"):
        lines.append(str(plan["summary"]).strip())
        lines.append("")
    if plan.get("rationale"):
        lines.append("**Why this approach:** " + str(plan["rationale"]).strip())
        lines.append("")
    if plan.get("assumptions"):
        lines.append("## Assumptions")
        for a in plan["assumptions"]:
            lines.append(f"- {a}")
        lines.append("")
    steps = plan.get("steps") or []
    if steps:
        lines.append("## Steps")
        for s in steps:
            n = s.get("n", "?")
            title = s.get("title") or f"step {n}"
            skill = s.get("skill")
            head = f"{n}. **{title}**" + (f" — `{skill}`" if skill else "")
            lines.append(head)
            if s.get("description"):
                lines.append(f"   {s['description']}")
            outs = s.get("expected_outputs") or []
            if outs:
                lines.append("   Expected outputs: " + ", ".join(outs))
            lines.append("")
    concerns = plan.get("concerns") or []
    if concerns:
        lines.append("## Validator concerns")
        for c in concerns:
            level = c.get("level", "info").upper()
            step_n = c.get("step_n")
            scope = f"step {step_n}" if step_n else "plan"
            lines.append(f"- *[{level}]* ({scope}) {c.get('message', '')}")
        lines.append("")
    lines.append(f"<!-- entity {plan_entity.get('id')} -->")
    return "\n".join(lines) + "\n"


def _claim_markdown(claim: dict) -> str:
    """Render a claim as .md prose for the materialized tree and zip."""
    meta = claim.get("metadata") or {}
    lines = [f"# {claim.get('title') or 'Claim'}", ""]
    if meta.get("statement"):
        lines.append(str(meta["statement"]))
        lines.append("")
    if meta.get("confidence"):
        lines.append(f"**Confidence:** {meta['confidence']}")
        lines.append("")
    for k in ("caveats", "evidence", "alternatives"):
        v = meta.get(k)
        if isinstance(v, list) and v:
            lines.append(f"## {k.capitalize()}")
            for item in v:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('text') or item.get('title') or '(item)'}")
                else:
                    lines.append(f"- {item}")
            lines.append("")
    lines.append(f"<!-- entity {claim.get('id')} -->")
    return "\n".join(lines) + "\n"


def _entity_mtime(e: dict) -> Optional[float]:
    """Parse created_at to a UNIX timestamp for ordering / setting mtime."""
    s = e.get("created_at") or ""
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _folder(name: str, *, path: str, entity: Optional[dict] = None, kind: str = "folder") -> dict:
    n: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "path": path,
        "children": [],
        "entity_id": entity["id"] if entity else None,
        "entity_type": entity["type"] if entity else None,
        "title": entity["title"] if entity else None,
        "mtime": _entity_mtime(entity) if entity else None,
    }
    return n


def _readme_node(*, path: str, container_kind: str, content: str, entity: Optional[dict] = None) -> dict:
    """A README.md node; content is computed by readme.py at this turn so
    the UI sees the same prose it would on materialize."""
    if not path:
        readme_path = "README.md"            # project root
    elif path.endswith("/"):
        readme_path = path + "README.md"
    else:
        readme_path = path + "/README.md"
    return {
        "kind": "readme",
        "name": "README.md",
        "path": readme_path,
        "entity_id": entity["id"] if entity else None,
        "entity_type": entity["type"] if entity else None,
        "content": content,
        "container_kind": container_kind,
        "size": len(content.encode("utf-8")),
        "mtime": _entity_mtime(entity) if entity else None,
    }


def _add_numbered_children(parent: dict, entities: list[dict], slug_fn=_folder_slug) -> list[tuple[dict, int, str]]:
    """Sort entities by created_at, assign NN_ prefixes, return (entity,
    index, full_segment) tuples for the caller to build deeper structure
    against."""
    sorted_es = sorted(entities, key=lambda e: e.get("created_at") or "")
    out = []
    for i, e in enumerate(sorted_es, start=1):
        seg = f"{i:02d}_{slug_fn(e)}"
        out.append((e, i, seg))
    return out


def _graft_dir(parent: dict, base, *, ephemeral: bool = True,
               skip: frozenset = frozenset(), skip_dirs: tuple = (),
               cap: int = 300, counter: Optional[list] = None) -> int:
    """Graft `base`'s files as a NESTED folder tree under `parent` (mirroring the
    on-disk layout, so a pile is navigable). Skips files whose resolved path is in
    `skip` or lives under any prefix in `skip_dirs`. Returns the count grafted.
    `counter` (a 1-elem list) shares a budget across calls."""
    from pathlib import Path as _P
    import os as _os
    base = _P(base)
    if not base.exists():
        return 0
    folders: dict[str, dict] = {parent["path"]: parent}

    def _ensure(parts: list[str]) -> dict:
        path, node = parent["path"], parent
        for p in parts:
            path = f"{path}/{p}" if path else p
            child = folders.get(path)
            if child is None:
                child = _folder(p, path=path, kind="folder")
                if ephemeral:
                    child["ephemeral"] = True
                node["children"].append(child)
                folders[path] = child
            node = child
        return node

    cnt = 0
    for f in sorted(base.rglob("*")):     # sorted → stable, parents before children
        if (counter[0] if counter else cnt) >= cap:
            break
        if not f.is_file() or f.name.startswith("."):
            continue
        rp = str(f.resolve())
        if rp in skip or any(rp == d or rp.startswith(d + _os.sep) for d in skip_dirs):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        rel = f.relative_to(base).parts                # (...subdirs..., filename)
        node = _ensure(list(rel[:-1]))
        fnode = {
            "kind": "file", "name": f.name,
            "path": f"{node['path']}/{f.name}",
            "artifact_path": rp, "size": st.st_size, "mtime": st.st_mtime,
        }
        if ephemeral:
            fnode["ephemeral"] = True
        node["children"].append(fnode)
        cnt += 1
        if counter is not None:
            counter[0] += 1
    return cnt


def _graft_run_outputs(parent: dict, run: dict, *, cap: int = 300) -> int:
    """Graft a Run's output/ from the PRODUCED LEDGER (exec-record produced[]
    + retention states via run_durable_view), then top up from the run's
    artifact_path on disk for anything the ledger doesn't list.

    The ledger is primary because under the weft-kernel substrate produced
    files live in the KERNEL WORKSPACE, not in artifact_path — the old
    disk-walk-only source silently showed an empty output/ for every kernel
    run (bulk .h5ad/.zarr outputs invisible in the Files tab while the Run
    card, which reads the ledger, listed them). The disk top-up keeps legacy
    jobdir runs (whose files really do land in artifact_path) fully listed.

    Honesty rules, matching the durable panel: `cleared` files don't render
    (the tab lists files that exist; the Run card owns the discard
    narrative); `in-sandbox`/`at-risk`/`unknown` are marked ephemeral (an
    address that dies with its sandbox must say so); a cap cut is DECLARED
    on the parent node, never silent. File nodes carry `run_id` + `rel` so
    the serve/materialize doors can resolve bytes through the canonical
    run resolver instead of trusting a URL shape."""
    from pathlib import Path as _P
    rid = run["id"]
    listed: list[str] = []
    cnt = 0
    view_files: list[dict] = []
    try:
        from core.exec.artifacts import artifacts_for_run
        if artifacts_for_run(rid):     # cheap local gate: no ledger rows →
            from content.bio.lifecycle.runs import run_durable_view
            view_files = [f for f in run_durable_view(rid)["files"]
                          if f.get("state") != "cleared"]
    except Exception:  # noqa: BLE001 — substrate trouble ≠ empty run; the
        pass           # disk top-up below still lists what's locally visible

    folders: dict[str, dict] = {parent["path"]: parent}

    def _ensure(parts: list[str]) -> dict:
        path, node = parent["path"], parent
        for p in parts:
            path = f"{path}/{p}"
            child = folders.get(path)
            if child is None:
                child = _folder(p, path=path, kind="folder")
                node["children"].append(child)
                folders[path] = child
            node = child
        return node

    for f in view_files[:cap]:
        rel = f.get("rel") or ""
        if not rel:
            continue
        parts = rel.split("/")
        node = _ensure(parts[:-1])
        fnode: dict = {
            "kind": "file", "name": parts[-1],
            "path": f"{node['path']}/{parts[-1]}",
            "artifact_path": f.get("url"),
            "size": f.get("bytes"),
            "state": f.get("state"), "badge": f.get("badge"),
            "run_id": rid, "rel": rel,
        }
        if f.get("site"):
            fnode["site"] = f["site"]
        if f.get("state") in ("in-sandbox", "at-risk", "unknown"):
            fnode["ephemeral"] = True
        node["children"].append(fnode)
        listed.append(rel)
        cnt += 1
    if len(view_files) > cap:
        parent["truncated"] = True
        parent["note"] = (f"showing {cap} of {len(view_files)} produced files "
                          f"— open the Run for the full list")

    out_dir = run.get("artifact_path")
    if out_dir and cnt < cap:
        try:
            base = _P(out_dir)
            # rels already listed from the ledger + the sandbox scaffolding a
            # job runner writes at its root (process bookkeeping, not products
            # — the run log has its own surfaces; one owner for the name set:
            # the durable view folds the same rels). blocks/ is the execution
            # transcript, folded at the ledger level for the same reason.
            from content.bio.lifecycle.runs import _RUNNER_SCAFFOLDING
            skip = frozenset(
                [str((base / rel).resolve()) for rel in listed]
                + [str((base / n).resolve()) for n in _RUNNER_SCAFFOLDING])
            skip_dirs = (str((base / "blocks").resolve()),)
        except Exception:  # noqa: BLE001
            skip, skip_dirs = frozenset(), ()
        cnt += _graft_dir(parent, out_dir, ephemeral=False, skip=skip,
                          skip_dirs=skip_dirs, cap=cap - cnt)
    return cnt


def _run_output_dirs(entities: list[dict]) -> tuple:
    """Resolved output-dir paths of all analysis Runs (their artifact_path) — so
    the catch-all working/ node can skip files that belong under a Run."""
    from pathlib import Path as _P
    out = []
    for e in entities:
        # Skip the ambient analysis: it's hidden from the runs UI, so its dir
        # (the shared thread scratch) must stay visible under working/.
        if (e.get("type") == "analysis" and e.get("artifact_path")
                and not (e.get("metadata") or {}).get("ambient")):
            try:
                out.append(str(_P(e["artifact_path"]).resolve()))
            except Exception:  # noqa: BLE001
                pass
    return tuple(out)


def _working_files_node(entities: list[dict]) -> Optional[dict]:
    """A 'working' folder listing real on-disk files (the project's data + work
    dirs) that AREN'T registered entities and don't belong to a Run — so nothing
    the agent produces is hidden, without duplicating per-Run output (which
    shows under threads/<t>/runs/<r>/output/). Flagged ephemeral: scratch is GC-able."""
    from pathlib import Path as _P
    try:
        from core.config import project_data_dir, project_work_dir
    except Exception:  # noqa: BLE001
        return None
    pid = "_workspace"
    try:
        from core.projects import current as _cur
        pid = _cur() or "_workspace"
    except Exception:  # noqa: BLE001
        pass
    shown = frozenset(
        str(_P(d).resolve())
        for e in entities
        for d in (_resolve_disk(e.get("artifact_path")),) if d
    )
    run_dirs = _run_output_dirs(entities)          # Run output dirs live under projects/<pid>/work/
    root = _folder("working", path="working", kind="folder")
    root["ephemeral"] = True
    root["note"] = "Scratch / unregistered files on disk — not kept unless promoted to a dataset."
    budget = [0]
    for base, label in ((_P(project_data_dir(pid)), "data"), (_P(project_work_dir(pid)), "scratch")):
        if not base.exists():
            continue
        label_node = _folder(label, path=f"working/{label}", kind="folder")
        label_node["ephemeral"] = True
        _graft_dir(label_node, base, ephemeral=True, skip=shown, skip_dirs=run_dirs,
                   cap=200, counter=budget)
        if label_node["children"]:
            root["children"].append(label_node)
    if not root["children"]:
        return None
    return root


def build_files_tree(*, include_archived: bool = False) -> dict:
    """Compose the full project file tree from the entity graph.

    Returns one root node. Each node has `kind` (root/folder/file/readme),
    `name`, `path` (relative POSIX), `children` (for folders), and
    entity backing (when one exists).
    """
    # README generation lazily imports — keeps tree.py free of bio coupling.
    from content.bio.files.readme import render_readme  # noqa: WPS433

    entities = [e for e in list_entities(include_archived=include_archived) if e["type"] != "workspace"]
    by_id: dict[str, dict] = {e["id"]: e for e in entities}

    # Group entities for the top-level layout.
    threads = [e for e in entities if e["type"] in THREAD_TYPES]
    datasets = [e for e in entities if e["type"] == "dataset"]
    findings = [e for e in entities if e["type"] in FINDING_TYPES]

    def in_thread(tid: Optional[str], types: set[str]) -> list[dict]:
        return [
            e for e in entities
            if e["type"] in types
            and (e.get("metadata") or {}).get("thread_id") == tid
        ]

    def run_children(run_id: str) -> list[dict]:
        out = []
        for edge in edges_to(run_id):
            if edge["rel_type"] != "wasGeneratedBy":
                continue
            child = by_id.get(edge["source_id"])
            if child:
                out.append(child)
        return out

    def result_members(result: dict) -> list[dict]:
        """Yield (entity_dict, member_dict) for each member that resolves
        to a known entity; text-only members yield (None, member_dict).
        """
        meta = result.get("metadata") or {}
        for m in (meta.get("members") or []):
            ref = m.get("ref")
            ent = by_id.get(ref) if ref else None
            yield ent, m

    # Track entities that are placed somewhere via thread/run/result so
    # we know what's an orphan at the end.
    placed_in_thread: set[str] = set()
    placed_via_run: set[str] = set()
    placed_via_result: set[str] = set()

    root = _folder("", path="", kind="root")
    # Project README is rendered by content; for now stub it with a
    # placeholder describing the project (later: from a Project entity).
    root["children"].append(_readme_node(
        path="",
        container_kind="project",
        content=render_readme("project", root_entities=entities),
    ))

    # ---------- datasets/ ----------
    if datasets:
        from pathlib import Path as _P
        d_folder = _folder("datasets", path="datasets")
        for d in sorted(datasets, key=lambda x: x.get("created_at") or ""):
            placed_in_thread.add(d["id"])  # datasets aren't orphans
            ap = d.get("artifact_path")
            # Remote / by-reference home: the bytes are on another site, so build
            # the listing from the captured descriptor (real filenames) rather than
            # walking the controller's local disk — which holds nothing and would
            # fabricate a title-slug `.bin`.
            if _is_remote_dataset(d):
                d_folder["children"].append(_remote_dataset_node(d, _folder_slug(d)))
                continue
            disk = _resolve_disk(ap) if ap else None
            if disk and _P(disk).is_dir():
                # A directory dataset (e.g. a 10x bundle / multi-sample download):
                # show it as an entity-backed folder + graft its real contents so
                # it's browsable, not a single opaque "file" row.
                seg = _folder_slug(d)
                ds = _folder(seg, path=f"datasets/{seg}", entity=d)
                _graft_dir(ds, disk, ephemeral=False, cap=500)
                d_folder["children"].append(ds if ds["children"]
                                            else _file_node(d, path=f"datasets/{_leaf_name(d)}"))
            elif ap:
                d_folder["children"].append(_file_node(d, path=f"datasets/{_leaf_name(d)}"))
            # else: dataset without artifact — skip silently
        if d_folder["children"]:
            root["children"].append(d_folder)

    # ---------- threads/ ----------
    if threads:
        threads_folder = _folder("threads", path="threads")
        for thread, idx, t_seg in _add_numbered_children(threads_folder, threads):
            t_path = f"threads/{t_seg}"
            t_folder = _folder(t_seg, path=t_path, entity=thread)
            t_folder["children"].append(_readme_node(
                path=t_path,
                container_kind="thread",
                content=render_readme("thread", entity=thread),
                entity=thread,
            ))

            # Runs in this thread. Hide EMPTY ones (no outputs, no captured code):
            # a Run is opened the moment a plan is presented (default-save), so a
            # pending/abandoned plan would otherwise litter the tree with an empty
            # Run. It surfaces once it actually has content.
            # Exclude the ambient catch-all analysis (it's a structural parent for
            # ad-hoc outputs, not a real Run — hidden from the UI per its `ambient`
            # flag; its files surface under working/ and its figures as orphans).
            # Post-cutover: Run "has code?" comes from aggregated_code_for_run
            # (concatenation of all exec records in the Run). The legacy
            # producing_code column is no longer authoritative.
            from core.graph.exec_records import aggregated_code_for_run as _agg_code
            runs = [r for r in in_thread(thread["id"], RUN_TYPES)
                    if not (r.get("metadata") or {}).get("ambient")
                    and (run_children(r["id"]) or _agg_code(r["id"]))]
            if runs:
                runs_folder = _folder("runs", path=f"{t_path}/runs")
                for run, ri, r_seg in _add_numbered_children(runs_folder, runs):
                    placed_in_thread.add(run["id"])
                    r_path = f"{t_path}/runs/{r_seg}"
                    r_folder = _folder(r_seg, path=r_path, entity=run)
                    r_folder["children"].append(_readme_node(
                        path=r_path,
                        container_kind="run",
                        content=render_readme("run", entity=run, children=run_children(run["id"])),
                        entity=run,
                    ))

                    # Producing code for the Run = the aggregated code across
                    # every exec record this Run has dispatched. Single source
                    # of truth (the exec records); the legacy run.producing_code
                    # denormalization is gone.
                    code = _agg_code(run["id"])
                    if code:
                        r_folder["children"].append({
                            "kind": "file",
                            "name": "producing_code.py",
                            "path": f"{r_path}/producing_code.py",
                            "entity_id": run["id"],
                            "entity_type": "code",
                            "title": "Producing code",
                            "artifact_path": None,
                            "size": len(code.encode("utf-8")),
                            "mtime": _entity_mtime(run),
                            "synthesized": True,
                            "synthesized_kind": "producing_code",
                            "synthesized_content": code,
                        })

                    # Full output listing — every file the run PRODUCED, from
                    # the ledger (durable states included) plus any legacy
                    # on-disk files under artifact_path. This is the browsable
                    # bundle; the curated figures/tables below are the
                    # harvested subset (also kept as entities, pinnable).
                    out_node = _folder("output", path=f"{r_path}/output", kind="folder")
                    _graft_run_outputs(out_node, run, cap=300)
                    if out_node["children"]:
                        r_folder["children"].append(out_node)

                    # Group run's children by type into subfolders.
                    children = run_children(run["id"])
                    grouped: dict[str, list[dict]] = {}
                    for c in children:
                        placed_via_run.add(c["id"])
                        sub = RUN_SUBDIRS.get(c["type"], c["type"] + "s")
                        grouped.setdefault(sub, []).append(c)
                    for sub_name in sorted(grouped):
                        sub_path = f"{r_path}/{sub_name}"
                        sub_folder = _folder(sub_name, path=sub_path)
                        for c in sorted(grouped[sub_name], key=lambda x: x.get("created_at") or ""):
                            if c.get("artifact_path"):
                                sub_folder["children"].append(_file_node(c, path=f"{sub_path}/{_leaf_name(c)}"))
                        if sub_folder["children"]:
                            r_folder["children"].append(sub_folder)

                    runs_folder["children"].append(r_folder)
                t_folder["children"].append(runs_folder)

            # Results in this thread
            results = in_thread(thread["id"], RESULT_TYPES)
            if results:
                results_folder = _folder("results", path=f"{t_path}/results")
                for result, ri, res_seg in _add_numbered_children(results_folder, results):
                    placed_in_thread.add(result["id"])
                    res_path = f"{t_path}/results/{res_seg}"
                    res_folder = _folder(res_seg, path=res_path, entity=result)
                    members = list(result_members(result))
                    res_folder["children"].append(_readme_node(
                        path=res_path,
                        container_kind="result",
                        content=render_readme(
                            "result", entity=result,
                            members=[ent for ent, _ in members if ent is not None],
                        ),
                        entity=result,
                    ))
                    for ent, m in members:
                        if ent is None:
                            # Text-only panel — synthesize as a .md
                            cap = m.get("caption") or "note"
                            slug = slugify(cap)
                            text = m.get("text") or ""
                            note_path = f"{res_path}/{slug}.md"
                            res_folder["children"].append({
                                "kind": "file",
                                "name": f"{slug}.md",
                                "path": note_path,
                                "entity_id": None,
                                "entity_type": "result_note",
                                "title": cap,
                                "artifact_path": None,
                                "size": len(text.encode("utf-8")),
                                "mtime": _entity_mtime(result),
                                "synthesized": True,
                                "synthesized_kind": "result_text",
                                "synthesized_content": text,
                            })
                        elif ent.get("artifact_path"):
                            placed_via_result.add(ent["id"])
                            res_folder["children"].append(
                                _file_node(ent, path=f"{res_path}/{_leaf_name(ent)}")
                            )
                        # else: member that's not a file (rare); skip
                    results_folder["children"].append(res_folder)
                t_folder["children"].append(results_folder)

            # Claims in this thread (synthesized .md)
            claims = in_thread(thread["id"], CLAIM_TYPES)
            if claims:
                claims_folder = _folder("claims", path=f"{t_path}/claims")
                for claim, ci, c_seg in _add_numbered_children(claims_folder, claims):
                    placed_in_thread.add(claim["id"])
                    c_path = f"{t_path}/claims/{c_seg}.md"
                    md = _claim_markdown(claim)
                    claims_folder["children"].append({
                        "kind": "file",
                        "name": f"{c_seg}.md",
                        "path": c_path,
                        "entity_id": claim["id"],
                        "entity_type": "claim",
                        "title": claim.get("title"),
                        "artifact_path": None,
                        "size": len(md.encode("utf-8")),
                        "mtime": _entity_mtime(claim),
                        "synthesized": True,
                        "synthesized_kind": "claim",
                        "synthesized_content": md,
                    })
                t_folder["children"].append(claims_folder)

            # Plans in this thread (synthesized .md). Lifecycle (validated,
            # executing, completed, …) lives in metadata.plan_lifecycle;
            # the file shows the proposed steps + concerns as durable prose.
            plans = in_thread(thread["id"], PLAN_TYPES)
            if plans:
                plans_folder = _folder("plans", path=f"{t_path}/plans")
                for plan_e, pi, p_seg in _add_numbered_children(plans_folder, plans):
                    placed_in_thread.add(plan_e["id"])
                    p_path = f"{t_path}/plans/{p_seg}.md"
                    md = _plan_markdown(plan_e)
                    plans_folder["children"].append({
                        "kind": "file",
                        "name": f"{p_seg}.md",
                        "path": p_path,
                        "entity_id": plan_e["id"],
                        "entity_type": "plan",
                        "title": plan_e.get("title"),
                        "artifact_path": None,
                        "size": len(md.encode("utf-8")),
                        "mtime": _entity_mtime(plan_e),
                        "synthesized": True,
                        "synthesized_kind": "plan",
                        "synthesized_content": md,
                    })
                t_folder["children"].append(plans_folder)

            # Misc thread-scoped entities (notes, narratives) that aren't
            # otherwise placed.
            scoped_extras = [
                e for e in entities
                if (e.get("metadata") or {}).get("thread_id") == thread["id"]
                and e["type"] in ("note", "narrative")
            ]
            for x in scoped_extras:
                placed_in_thread.add(x["id"])
            # (Folder them later if they accumulate; for now skip to keep
            # the tree clean.)

            placed_in_thread.add(thread["id"])
            threads_folder["children"].append(t_folder)
        root["children"].append(threads_folder)

    # ---------- findings/ ----------
    if findings:
        findings_folder = _folder("findings", path="findings")
        for finding, idx, f_seg in _add_numbered_children(findings_folder, findings):
            f_path = f"findings/{f_seg}"
            f_folder = _folder(f_seg, path=f_path, entity=finding)
            f_folder["children"].append(_readme_node(
                path=f_path,
                container_kind="finding",
                content=render_readme("finding", entity=finding),
                entity=finding,
            ))
            placed_in_thread.add(finding["id"])
            findings_folder["children"].append(f_folder)
        root["children"].append(findings_folder)

    # ---------- orphans/ ----------
    orphan_files = [
        e for e in entities
        if e["id"] not in placed_in_thread
        and e["id"] not in placed_via_run
        and e["id"] not in placed_via_result
        and e["type"] in LEAF_TYPES
        and e.get("artifact_path")
    ]
    if orphan_files:
        orphans_folder = _folder("orphans", path="orphans")
        grouped: dict[str, list[dict]] = {}
        for e in orphan_files:
            sub = e["type"] + "s"
            grouped.setdefault(sub, []).append(e)
        for sub_name in sorted(grouped):
            sub_path = f"orphans/{sub_name}"
            sub_folder = _folder(sub_name, path=sub_path)
            for e in sorted(grouped[sub_name], key=lambda x: x.get("created_at") or ""):
                sub_folder["children"].append(_file_node(e, path=f"{sub_path}/{_leaf_name(e)}"))
            orphans_folder["children"].append(sub_folder)
        root["children"].append(orphans_folder)

    # Working files: real on-disk scratch/unregistered files, so they're visible
    # (not just the curated entity tree). Promotion elevates them to datasets.
    wf = _working_files_node(entities)
    if wf:
        root["children"].append(wf)

    _dedupe_child_paths(root)
    return root


def _dedupe_child_paths(node: dict) -> None:
    """Ensure sibling nodes have unique `path`s — two same-named artifacts in one
    folder (e.g. two 'untitled.csv' tables under runs/<r>/tables/) otherwise
    collide, which the frontend uses as a React key (dropped/duplicated rows) and
    which makes path-based download/view ambiguous. Suffix the dupes (·2, ·3).
    Recurses; artifact_path (disk resolution) is untouched."""
    seen: dict[str, int] = {}
    for c in node.get("children") or []:
        p = c.get("path")
        if p:
            n = seen.get(p, 0) + 1
            seen[p] = n
            if n > 1:
                c["path"] = f"{p}~{n}"
        _dedupe_child_paths(c)


def iter_files(node: dict, base_path: str = "") -> list[dict]:
    """Walk a tree node and yield every file/readme node beneath it,
    flattened. Used by the download endpoint."""
    out: list[dict] = []
    if node.get("kind") in ("file", "readme"):
        out.append(node)
        return out
    for child in node.get("children", []):
        out.extend(iter_files(child, base_path))
    return out


def find_node(tree: dict, path: str) -> Optional[dict]:
    """Return the node at the given path (e.g. 'threads/01_x/runs') or
    None if absent. Empty path returns the root."""
    target = path.strip("/")
    if not target:
        return tree

    def walk(node: dict) -> Optional[dict]:
        if node.get("path") == target:
            return node
        for c in node.get("children", []):
            r = walk(c)
            if r is not None:
                return r
        return None

    return walk(tree)


def find_file_node(tree: dict, path: str) -> Optional[dict]:
    """Resolve a file to its tree node TOLERANTLY: an exact path first, then a
    path-suffix or bare-basename match — so a bare `processed.h5ad` or a partial
    `output/processed.h5ad` resolves to the real node (an agent rarely knows the
    full tree path). On multiple matches, prefer a path-suffix hit over a mere
    basename, then the most recently modified. Returns None if nothing matches."""
    exact = find_node(tree, path)
    if exact is not None:
        return exact
    target = (path or "").strip("/")
    if not target:
        return None
    base = target.rsplit("/", 1)[-1]
    matches: list[dict] = []

    def collect(node: dict) -> None:
        p = node.get("path") or ""
        if p == target or p.endswith("/" + target) or node.get("name") == base:
            matches.append(node)
        for c in node.get("children", []):
            collect(c)

    collect(tree)
    if not matches:
        return None

    def score(n: dict):
        p = n.get("path") or ""
        is_suffix = (p == target or p.endswith("/" + target))
        return (1 if is_suffix else 0, n.get("mtime") or 0)

    matches.sort(key=score, reverse=True)
    return matches[0]


def list_file_matches(tree: dict, path: str, limit: int = 6) -> list[str]:
    """Tree paths whose basename matches `path`'s basename — for a clear
    'did you mean' error when resolution is ambiguous or the caller wants options."""
    base = (path or "").strip("/").rsplit("/", 1)[-1]
    out: list[str] = []

    def collect(node: dict) -> None:
        if node.get("name") == base and node.get("path"):
            out.append(node["path"])
        for c in node.get("children", []):
            collect(c)

    collect(tree)
    return out[:limit]
