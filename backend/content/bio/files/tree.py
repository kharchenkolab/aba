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
from core.files.registry import slugify, ext_from_artifact
from core.graph.entities import list_entities
from core.graph.edges import edges_to


# Per-bio-type categorization the tree composer cares about. Content
# could register these instead — for the MVP they're a constant.
LEAF_TYPES = {"figure", "table", "dataset", "note", "narrative"}
CLAIM_TYPES = {"claim"}
RESULT_TYPES = {"result"}
RUN_TYPES = {"analysis"}
THREAD_TYPES = {"thread"}
FINDING_TYPES = {"finding"}

# Where a leaf lives under a run by default (subdir name → entity type).
RUN_SUBDIRS = {"figure": "figures", "table": "tables"}

# Synthesized-text extensions.
PROSE_EXTS = {"note": ".md", "narrative": ".md", "claim": ".md"}


def _resolve_disk(artifact_path: Optional[str]) -> Optional[Path]:
    if not artifact_path:
        return None
    if artifact_path.startswith("/artifacts/"):
        return ARTIFACTS_DIR / Path(artifact_path).name
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


def _leaf_name(e: dict) -> str:
    """Filename for a leaf entity. Uses title slug + extension lookup."""
    slug = slugify(e.get("title") or e.get("id") or "untitled")
    t = e.get("type") or ""
    if t in PROSE_EXTS:
        return f"{slug}{PROSE_EXTS[t]}"
    ext = ext_from_artifact(e, default=".bin")
    return f"{slug}{ext}"


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
        d_folder = _folder("datasets", path="datasets")
        for d in sorted(datasets, key=lambda x: x.get("created_at") or ""):
            placed_in_thread.add(d["id"])  # datasets aren't orphans
            if d.get("artifact_path"):
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

            # Runs in this thread
            runs = in_thread(thread["id"], RUN_TYPES)
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

                    # Producing code if present on the run entity.
                    code = run.get("producing_code")
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

    return root


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
