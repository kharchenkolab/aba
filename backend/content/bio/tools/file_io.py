"""Bio file I/O cluster — DATA_DIR enumeration, upload inspection,
project-scoped file read/write/edit (WU-3-tail).

list_data_files + inspect_upload (and its describe/sniff/loader-suggest
helpers) form the "what's in the workspace" surface. write_file_tool,
edit_file_tool, read_file_tool enforce path containment inside
DATA_DIR / WORK_DIR / ARTIFACTS_DIR (absolute paths outside are
refused). _registered_datasets is shared with the dispatcher (still
in __init__.py) via re-export."""

from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import Optional


# write/edit/read_file size caps. Bounded both to protect the agent's
# context window (multi-MB writes burn tokens fast) and to keep
# accidental whole-binary reads cheap.
_FILE_TOOL_MAX_BODY = 5 * 1024 * 1024     # write_file body cap
_FILE_TOOL_MAX_FILE = 10 * 1024 * 1024    # edit_file in-memory cap
_FILE_TOOL_MAX_READ_BYTES = 200_000       # read_file output cap
_FILE_TOOL_MAX_READ_LINES = 5000          # read_file line cap


# Recognized file extensions and their semantic types.
_TYPE_MAP = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".h5ad": "h5ad (AnnData)",
    ".h5": "h5",
    ".loom": "loom",
    ".mtx": "matrix-market",
    ".rds": "R-serialized",
    ".fastq": "fastq",
    ".fq": "fastq",
    ".fa": "fasta",
    ".fasta": "fasta",
    ".bam": "bam",
    ".vcf": "vcf",
    ".json": "json",
    ".parquet": "parquet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "markdown",
}


def list_data_files(_input: dict) -> dict:
    """The project's datasets — the Data facet, NOT the global data folder. Data
    files are stored globally (content-addressed) but each project only *contains*
    the datasets registered as entities in its DB, so we list those."""
    from pathlib import Path as _Path
    files = []
    registered_names = set()
    for d in _registered_datasets():
        path = d.get("path")
        name = d.get("name", "")
        if name:
            registered_names.add(name)
        size = None
        try:
            if path and _Path(path).exists():
                size = _Path(path).stat().st_size
        except Exception:
            pass
        files.append({"filename": name, "size_bytes": size,
                      "path": str(path) if path else None,
                      "title": d.get("title"), "registered": True})

    # Also surface data files sitting in DATA_DIR that aren't registered as
    # datasets — otherwise the agent sees "no datasets", concludes the project
    # is empty, and asks the user to upload files that are already present.
    # These are readable directly by filename (read_csv_info / run_python).
    _DATA_EXTS = {".csv", ".tsv", ".tab", ".txt", ".xlsx", ".parquet",
                  ".h5ad", ".h5", ".loom", ".mtx", ".gz", ".tar", ".zip", ".fa", ".fasta"}
    n_unregistered = 0
    from core.config import current_project_id, project_data_dir
    _data_dir = project_data_dir(current_project_id())
    try:
        for p in sorted(_data_dir.iterdir()):
            if not p.is_file() or p.name in registered_names:
                continue
            if p.suffix.lower() not in _DATA_EXTS:
                continue
            files.append({"filename": p.name, "size_bytes": p.stat().st_size,
                          "path": str(p), "registered": False})
            n_unregistered += 1
    except Exception:
        pass

    if not files:
        return {"files": [], "message": "This project has no datasets yet — ask the user to upload one."}
    # The message has to match where the files ACTUALLY are. A dataset can be
    # registered from anywhere (work/, an absolute path) — not just DATA_DIR —
    # so the DATA_DIR convention applies only when every listed file sits
    # inside DATA_DIR. Otherwise the agent should use the absolute `path`
    # field verbatim (2026-05-31 live: the DATA_DIR template said one thing
    # while the registered path lived in work/, the agent tried DATA_DIR/X,
    # missed, then needed an extra turn to recover).
    data_dir_str = str(_data_dir)
    all_in_data_dir = all((f.get("path") or "").startswith(data_dir_str + "/") for f in files)
    if all_in_data_dir:
        message = ("Load these via the DATA_DIR variable (already defined in run_python): "
                   "e.g. pd.read_csv(f'{DATA_DIR}/<filename>'). Use the listed `path` "
                   "values directly — do not hardcode other directories.")
    else:
        message = ("Use the listed `path` values directly — they're absolute paths. "
                   "These datasets do NOT live in DATA_DIR (e.g. they were registered "
                   "from a work scratch dir or an explicit absolute path); the "
                   "DATA_DIR/<filename> shortcut won't resolve.")
    out = {"files": files, "data_dir": data_dir_str, "message": message}
    return out


def _registered_datasets() -> list[dict]:
    """List of {name, path, title} for the project's registered datasets.
    Shared by list_data_files and inspect_upload — the latter uses it to
    auto-resolve a constructed-from-prior path to the real registered
    path when basenames match (2026-05-31: live-session bug where the
    agent saw the right path in list_data_files but then built a
    DATA_DIR-shaped path from prior and hit "path not found")."""
    from core.graph.entities import list_entities
    out = []
    for e in list_entities(include_archived=False):
        if e.get("type") != "dataset":
            continue
        path = e.get("artifact_path")
        name = Path(path).name if path else (e.get("title") or "")
        if name:
            out.append({"name": name, "path": path, "title": e.get("title")})
    return out


def inspect_upload(input_: dict) -> dict:
    """
    Inspect a file or directory. Auto-extracts archives.

    Path resolution (in order):
      1. Absolute path that exists inside DATA_DIR → use as-is.
      2. Relative path → resolved against DATA_DIR; if it exists, use.
      3. Otherwise, look up registered datasets by basename. If exactly
         ONE matches, auto-resolve to its `artifact_path` (which may
         live in work/ or any absolute location) and continue with a
         `path_corrected` field in the result. This handles the case
         where the agent constructs a DATA_DIR-shaped path from prior
         instead of using the `path` field returned by list_data_files.
      4. No unambiguous match → return error WITH the list of
         registered datasets so the agent can pick one.

    Returns:
      {
        "root": "<resolved absolute path>",
        "kind": "file" | "directory" | "archive",
        "extracted_to": "<dir>",          # only when archive
        "files": [{"path": ..., "size": ..., "type": ...}, ...],
        "suggested_loader": "<text>",
        "summary": "<one line description>",
        "path_corrected": {"from": "<raw>", "to": "<resolved>",
                           "reason": "..."}   # only when auto-resolved
      }
    """
    import tarfile
    import zipfile
    raw = input_.get("path", "")
    if not raw:
        return {"error": "path is required"}
    p = Path(raw)
    from core.config import current_project_id, project_data_dir
    _data_dir = project_data_dir(current_project_id())
    if not p.is_absolute():
        p = _data_dir / p
    # Snapshot the registered set once — used both for the
    # "absolute path matches a registered dataset" accept and for
    # the basename-match auto-resolve fallback.
    registered = _registered_datasets()
    registered_paths = {d.get("path"): d for d in registered if d.get("path")}
    resolved: Optional[Path] = None
    # 1. Exact match against a registered artifact_path — accept even
    #    if it sits outside DATA_DIR (registered datasets are allowed
    #    to live in work/ or any absolute location).
    try:
        rp = p.resolve()
        if str(rp) in registered_paths and rp.exists():
            resolved = rp
    except FileNotFoundError:
        rp = None
    # 2. Path resolves under DATA_DIR and exists — typical local upload.
    if resolved is None and rp is not None:
        if rp.exists() and str(rp).startswith(str(_data_dir.resolve())):
            resolved = rp
    # 2b. Path resolves under REFS_DIR and exists — content-addressed shared
    # references the user uploaded directly (e.g. /workspace/aba-runtime/refs/
    # GSE192391/...). Treat the refs tree as a third trusted root so the
    # agent can `inspect_upload` files placed there before a reference
    # entity exists. Tightening (proper reference-registration flow) is
    # deferred — for now, accept any existing path under REFS_DIR.
    if resolved is None and rp is not None:
        try:
            from core.config import REFS_DIR
            refs_root = REFS_DIR.resolve()
            if rp.exists() and str(rp).startswith(str(refs_root)):
                resolved = rp
        except Exception:
            pass

    path_corrected: Optional[dict] = None
    if resolved is None:
        # 3. Auto-resolve by basename match against registered datasets.
        requested_basename = Path(raw).name
        matches = [d for d in registered
                   if d.get("name") == requested_basename and d.get("path")]
        if len(matches) == 1:
            mp = Path(matches[0]["path"])
            if mp.exists():
                resolved = mp.resolve()
                path_corrected = {
                    "from": raw,
                    "to": str(resolved),
                    "reason": ("dataset is registered at a different location "
                               "(work-dir / absolute path); auto-resolved by "
                               "basename. Use this `to` path verbatim next time "
                               "— don't reconstruct DATA_DIR paths from prior."),
                }
        if resolved is None:
            # Give the agent the actual options so it can pick on retry.
            return {
                "error": f"path not found: {raw}",
                "hint": ("Don't construct DATA_DIR paths from prior — the "
                         "dataset may live in work/ or another absolute "
                         "location. Use one of the `path` values listed "
                         "below verbatim, or call list_data_files()."),
                "registered_datasets": [
                    {"name": d["name"], "path": d["path"], "title": d.get("title")}
                    for d in registered
                ],
            }
    p = resolved

    # Auto-extract archives.
    if p.is_file() and (
        p.suffix in (".tar", ".zip")
        or p.name.endswith(".tar.gz")
        or p.name.endswith(".tgz")
    ):
        ext_dir = p.with_suffix("").with_suffix("") if p.name.endswith(".tar.gz") else p.with_suffix("")
        ext_dir = Path(str(ext_dir) + "_extracted")
        if not ext_dir.exists():
            ext_dir.mkdir(parents=True)
            try:
                if zipfile.is_zipfile(p):
                    with zipfile.ZipFile(p) as zf:
                        zf.extractall(ext_dir)
                else:
                    with tarfile.open(p) as tf:
                        tf.extractall(ext_dir, filter="data")
            except Exception as e:
                return {"error": f"extraction failed: {e}"}
        out = _describe_directory(ext_dir, kind="archive", extracted_to=str(ext_dir),
                                  original_path=str(p))
        if path_corrected: out["path_corrected"] = path_corrected
        return out

    if p.is_dir():
        out = _describe_directory(p, kind="directory")
        if path_corrected: out["path_corrected"] = path_corrected
        return out

    # Single file.
    out = {
        "root": str(p),
        "kind": "file",
        "files": [_describe_file(p)],
        "suggested_loader": _suggest_single_loader(p),
        "summary": f"single file: {p.name} ({_fmt_size(p.stat().st_size)})",
    }
    if path_corrected: out["path_corrected"] = path_corrected
    return out


def _describe_directory(root: Path, *, kind: str = "directory",
                        extracted_to: Optional[str] = None,
                        original_path: Optional[str] = None) -> dict:
    """Walk a directory tree and produce a structured listing."""
    files = []
    for f in sorted(root.rglob("*")):
        if f.is_file():
            rel = f.relative_to(root)
            files.append(_describe_file(f, rel_path=str(rel)))
        # Skip directory entries — implied by files' paths.
    summary = _summarize_files(files)
    suggested = _suggest_loader_for_files(files, root)
    result = {
        "root": str(root),
        "kind": kind,
        "files": files,
        "suggested_loader": suggested,
        "summary": summary,
    }
    if extracted_to:
        result["extracted_to"] = extracted_to
    if original_path:
        result["original_path"] = original_path
    return result


def _describe_file(p: Path, rel_path: Optional[str] = None) -> dict:
    return {
        "path": rel_path or p.name,
        "size": p.stat().st_size,
        "type": _sniff_type(p),
    }


def _sniff_type(p: Path) -> str:
    name = p.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "archive/tar.gz"
    if name.endswith(".gz"):
        return f"{_TYPE_MAP.get(p.with_suffix('').suffix.lower(), 'binary')}+gzip"
    return _TYPE_MAP.get(p.suffix.lower(), "binary")


def _summarize_files(files: list[dict]) -> str:
    if not files:
        return "empty directory"
    types = {}
    total_bytes = 0
    for f in files:
        types[f["type"]] = types.get(f["type"], 0) + 1
        total_bytes += f["size"]
    bits = ", ".join(f"{n} {t}" for t, n in sorted(types.items(), key=lambda x: -x[1])[:5])
    return f"{len(files)} files ({bits}); {_fmt_size(total_bytes)} total"


def _common_parent(files: list[dict], filenames: set[str], root: Path) -> Optional[Path]:
    """Find the directory containing all of `filenames` (case-insensitive)."""
    parents_per_name: dict[str, set[Path]] = {n: set() for n in filenames}
    for f in files:
        rel = Path(f["path"])
        name = rel.name.lower()
        if name in filenames:
            parents_per_name[name].add((root / rel).parent.resolve())
    if not all(parents_per_name.values()):
        return None
    common = set.intersection(*parents_per_name.values())
    if not common:
        return None
    return next(iter(common))


def _suggest_loader_for_files(files: list[dict], root: Path) -> str:
    types = {f["type"] for f in files}

    # 10x Genomics v2 cellranger output: matrix.mtx + barcodes.tsv + genes.tsv
    parent_v2 = _common_parent(files, {"matrix.mtx", "barcodes.tsv", "genes.tsv"}, root)
    if parent_v2:
        return (
            "10x v2 cellranger output detected. Load with:\n"
            "    import scanpy as sc\n"
            f"    adata = sc.read_10x_mtx('{parent_v2}', var_names='gene_symbols')\n"
        )
    # 10x v3: matrix.mtx.gz + barcodes.tsv.gz + features.tsv.gz
    parent_v3 = _common_parent(
        files, {"matrix.mtx.gz", "barcodes.tsv.gz", "features.tsv.gz"}, root,
    )
    if parent_v3:
        return (
            "10x v3 cellranger output detected. Load with:\n"
            "    import scanpy as sc\n"
            f"    adata = sc.read_10x_mtx('{parent_v3}')\n"
        )
    if "h5ad (AnnData)" in types:
        h5ad = next(f for f in files if f["type"] == "h5ad (AnnData)")
        return f"AnnData file. Load with: import anndata; adata = anndata.read_h5ad('{root}/{h5ad['path']}')"
    if "csv" in types and len(files) == 1:
        return "Single CSV. Load with: import pandas as pd; df = pd.read_csv(...)"
    return "Multiple files; no single suggested loader. Inspect manually."


def _suggest_single_loader(p: Path) -> str:
    t = _sniff_type(p)
    if t == "csv":
        return f"import pandas as pd; df = pd.read_csv('{p}')"
    if t == "tsv":
        return f"import pandas as pd; df = pd.read_csv('{p}', sep='\\t')"
    if t == "h5ad (AnnData)":
        return f"import anndata; adata = anndata.read_h5ad('{p}')"
    return f"# {p.name}: type={t}; choose a loader manually"


def _fmt_size(n: int) -> str:
    if n < 1024: return f"{n} B"
    if n < 1024 * 1024: return f"{n/1024:.1f} KB"
    if n < 1024**3: return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.1f} GB"


def _resolve_project_path(path_str: str, ctx: dict | None,
                          must_exist: bool = False,
                          enforce_sandbox: bool = True) -> tuple[str, str | None]:
    """Resolve a file path for write_file / edit_file / read_file.
    Returns (abspath, error_or_None).

    With `enforce_sandbox=True` (default — write_file / edit_file): refuses paths
    outside this project's WORK_DIR/<pid>/ or DATA_DIR/<pid>/. Modifications
    have to stay inside the project the agent is working in, so a stray
    `/etc/passwd` write or stomping on another project's data is impossible.

    With `enforce_sandbox=False` (read_file): no sandbox — read anything the
    server process has filesystem access to. Lets the agent inspect global
    skills/recipes, log files, shared references, etc. without a round-trip
    through run_python. Reads are inherently safe; the only risk is the agent
    being misled by content it didn't ask for, which is its own problem."""
    from core import projects
    from core.config import project_work_dir, project_data_dir
    if not isinstance(path_str, str) or not path_str.strip():
        return "", "path is required"
    raw = path_str.strip()
    pid = (projects.current() or "default")
    work_root = project_work_dir(pid).resolve()
    data_root = project_data_dir(pid).resolve()

    p = Path(raw)
    if not p.is_absolute():
        # Relative: anchor at the active run's cwd if one is open, else the
        # thread's shared scratch.
        anchor: Path | None = None
        try:
            tid = str((ctx or {}).get("thread_id") or "")
            if tid:
                # Run cwd if a run is open, else scratch.
                from content.bio.lifecycle.runs import active_run_id
                from core.data.workspace import scratch_dir
                rid = active_run_id(tid)
                if rid:
                    # Mirror run_python's _run_scratch_cwd convention.
                    anchor = (work_root / rid).resolve()
                else:
                    anchor = scratch_dir(pid, f"thread-{tid}").resolve()
        except Exception:  # noqa: BLE001
            anchor = None
        if anchor is None:
            anchor = work_root
        p = (anchor / raw).resolve()
    else:
        p = p.resolve()

    if enforce_sandbox:
        # Sandbox: must be under work_root or data_root for THIS project.
        try:
            is_work = p.is_relative_to(work_root)
            is_data = p.is_relative_to(data_root)
        except AttributeError:
            # py<3.9 fallback (shouldn't apply — server is on 3.12).
            s = str(p)
            is_work = s.startswith(str(work_root) + os.sep) or s == str(work_root)
            is_data = s.startswith(str(data_root) + os.sep) or s == str(data_root)
        if not (is_work or is_data):
            return "", (f"path is outside the project sandbox; allowed roots: "
                        f"{work_root} (WORK_DIR) and {data_root} (DATA_DIR). "
                        f"Got: {p}")
    if must_exist and not p.exists():
        return "", f"file not found: {p}"
    return str(p), None


def write_file_tool(input_: dict, ctx: dict | None = None) -> dict:
    body = input_.get("body")
    if not isinstance(body, str):
        return {"error": "body is required (string)"}
    if len(body.encode("utf-8")) > _FILE_TOOL_MAX_BODY:
        return {"error": f"body exceeds {_FILE_TOOL_MAX_BODY} bytes — write a smaller chunk or use edit_file"}
    mode = (input_.get("mode") or "w").strip()
    if mode not in ("w", "a"):
        return {"error": f"mode must be 'w' or 'a' (got {mode!r})"}
    overwrite = bool(input_.get("overwrite", False))
    abspath, err = _resolve_project_path(input_.get("path") or "", ctx)
    if err:
        return {"error": err}
    p = Path(abspath)
    was_existing = p.exists()
    if mode == "w" and was_existing and not overwrite:
        return {"error": (f"file already exists; refusing to overwrite without "
                          f"overwrite=true. Path: {abspath}. To change part of "
                          f"the file, use edit_file. To append, use mode='a'.")}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, mode, encoding="utf-8") as f:
            f.write(body)
    except OSError as e:
        return {"error": f"write failed: {e}", "path": abspath}
    return {"status": "ok", "path": abspath, "bytes_written": len(body.encode("utf-8")),
            "was_existing": was_existing, "mode": mode}


def edit_file_tool(input_: dict, ctx: dict | None = None) -> dict:
    old_string = input_.get("old_string")
    new_string = input_.get("new_string")
    if not isinstance(old_string, str) or old_string == "":
        return {"error": "old_string is required (non-empty string)"}
    if not isinstance(new_string, str):
        return {"error": "new_string is required (string)"}
    replace_all = bool(input_.get("replace_all", False))
    abspath, err = _resolve_project_path(input_.get("path") or "", ctx, must_exist=True)
    if err:
        return {"error": err}
    p = Path(abspath)
    try:
        if p.stat().st_size > _FILE_TOOL_MAX_FILE:
            return {"error": f"file too large for edit_file ({p.stat().st_size} bytes; cap {_FILE_TOOL_MAX_FILE})", "path": abspath}
        with open(p, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"read failed: {e}", "path": abspath}
    count = content.count(old_string)
    if count == 0:
        return {"error": "old_string not found in file — check exact bytes "
                          "(whitespace, line endings) and try again",
                "path": abspath}
    if count > 1 and not replace_all:
        return {"error": (f"old_string is ambiguous: matches {count} times. "
                          "Include more surrounding context to make it unique, "
                          "or set replace_all=true to change every occurrence."),
                "path": abspath, "matches": count}
    new_content = (content.replace(old_string, new_string) if replace_all
                   else content.replace(old_string, new_string, 1))
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as e:
        return {"error": f"write failed: {e}", "path": abspath}
    replacements = count if replace_all else 1
    return {"status": "ok", "path": abspath, "replacements": replacements,
            "bytes_written": len(new_content.encode("utf-8"))}


def _open_text_streaming(path: Path):
    """Open a text or text-in-compressed-container file for streaming line reads.
    Transparently handles .gz / .bz2 / .xz. Caller is responsible for the with-
    statement. Returns an open file-like object yielding str lines."""
    name = path.name.lower()
    if name.endswith(".gz"):
        import gzip
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    if name.endswith(".bz2"):
        import bz2
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    if name.endswith(".xz"):
        import lzma
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def read_file_tool(input_: dict, ctx: dict | None = None) -> dict:
    offset = input_.get("offset") or 1
    limit = input_.get("limit")
    try:
        offset = max(1, int(offset))
    except (TypeError, ValueError):
        return {"error": f"offset must be a positive integer (got {offset!r})"}
    try:
        limit = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        return {"error": f"limit must be an integer if provided (got {input_.get('limit')!r})"}
    if limit is not None and limit <= 0:
        return {"error": "limit must be a positive integer"}
    cap = min(limit, _FILE_TOOL_MAX_READ_LINES) if limit is not None else _FILE_TOOL_MAX_READ_LINES
    abspath, err = _resolve_project_path(input_.get("path") or "", ctx,
                                          must_exist=True, enforce_sandbox=False)
    if err:
        return {"error": err}
    p = Path(abspath)
    size_on_disk = p.stat().st_size if p.exists() else 0

    # Stream-read line-by-line so a 50 GB fastq.gz doesn't load into memory.
    # Auto-decompresses .gz/.bz2/.xz so the agent can `read_file` a fastq.gz to
    # peek at the format without round-tripping through `run_python` + gzip.
    kept: list[str] = []
    bytes_kept = 0
    truncated = False
    lines_seen = 0          # total lines visited (offset + kept + any beyond cap)
    skipped = offset - 1
    try:
        with _open_text_streaming(p) as f:
            for raw_line in f:
                lines_seen += 1
                if lines_seen <= skipped:
                    continue
                ln = len(raw_line.encode("utf-8"))
                if bytes_kept + ln > _FILE_TOOL_MAX_READ_BYTES:
                    truncated = True; break
                kept.append(raw_line); bytes_kept += ln
                if len(kept) >= cap:
                    # Don't keep walking — but check if the file has more
                    # by peeking one more line; cheap and gives `truncated`
                    # the right meaning ("more to read past this slice").
                    try: more = next(f); truncated = True  # noqa: F841
                    except StopIteration: pass
                    break
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"read failed: {e}", "path": abspath}

    body = "".join(kept)
    return {"status": "ok", "path": abspath, "body": body,
            "lines_returned": len(kept),
            "truncated": truncated,
            "offset": offset, "limit": limit,
            "bytes": size_on_disk,
            "compressed": any(p.name.lower().endswith(s) for s in (".gz", ".bz2", ".xz"))}
