import subprocess
import shutil
import uuid
import json
import sys
from pathlib import Path
from typing import Optional
from config import DATA_DIR, ARTIFACTS_DIR

# ---------- Tool schemas (passed to Claude API) ----------

TOOL_SCHEMAS = [
    {
        "name": "list_data_files",
        "description": "List the datasets in THIS project (the project's Data facet). Returns each dataset's filename and size. This is the data the user has added to this project — reason about these, not the wider filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "inspect_upload",
        "description": (
            "Inspect a file or directory in the data folder. Returns a "
            "structured description: file tree (recursive), sniffed types, "
            "and a suggested Python loader. Use this on opaque uploads "
            "(archives, multi-file directories) before deciding how to load. "
            "Archives (.tar, .tar.gz, .zip) are auto-extracted and the "
            "result describes the extracted contents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path relative to DATA_DIR (e.g. 'pbmc3k.tar.gz') or "
                        "an absolute path inside DATA_DIR."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_csv_info",
        "description": "Read a CSV file and return its shape, column names with dtypes, and first 5 rows as a markdown table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename of the CSV (just the name, not a path)"
                }
            },
            "required": ["filename"]
        }
    },
    {
        "name": "create_scenario",
        "description": (
            "Create a scenario variant of a figure by re-running its analysis "
            "with a modification the user described (e.g. 'cap mt_fraction at "
            "0.10', 'exclude sample S4'). Use this AFTER the user confirms a "
            "'what if' proposal. Pass the baseline figure's id, a short "
            "description of the change, and the modified Python code (start "
            "from the baseline's producing code and apply the change). The "
            "variant appears beside the baseline with a Compare toggle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "baseline_id": {"type": "string", "description": "id of the baseline figure"},
                "description": {"type": "string", "description": "short label for the change"},
                "code": {"type": "string", "description": "modified Python code that saves a .png"},
            },
            "required": ["baseline_id", "description", "code"],
        },
    },
    {
        "name": "get_provenance",
        "description": (
            "Get the upstream provenance of an entity — what data and analyses "
            "it was derived from. Use this to answer 'how did I get this?' / "
            "'what data was used to make this figure?'. Pass an entity id "
            "(e.g. the focused entity's id)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
    },
    {
        "name": "get_dependents",
        "description": (
            "Get the downstream dependents of an entity — what would need to be "
            "reconsidered or recomputed if this entity changed. Use this to "
            "answer 'if I change this, what else is affected?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
    },
    {
        "name": "run_python",
        "description": (
            "Execute Python code in a sandboxed subprocess. "
            "pandas, numpy, matplotlib, scanpy, anndata, leidenalg, and umap "
            "are available. The data folder is available as the variable "
            "DATA_DIR (a string path). Save any plots as plt.savefig('out.png') "
            "or any .png name — they will be captured and displayed. Print any "
            "text results you want returned. Default timeout is 90 seconds; for "
            "scRNA-seq / bulk-RNA pipelines that need more, set timeout_s "
            "explicitly (max 600s)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute"
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Optional timeout in seconds; capped at 600. Use larger values for full pipeline runs.",
                    "minimum": 5,
                    "maximum": 600,
                },
                "background": {
                    "type": "boolean",
                    "description": "Run as a background job instead of inline. Use for long pipelines (>30s) so the conversation isn't blocked. Returns a job_id immediately; the figures register when the job finishes. Tell the user to watch the Queues panel.",
                },
                "title": {
                    "type": "string",
                    "description": "Short label for the job (shown in the Queues panel). Only used when background=true.",
                }
            },
            "required": ["code"]
        }
    },
    {
        "name": "present_plan",
        "description": (
            "Present a structured plan to the user BEFORE doing multi-step analysis "
            "or exploration, and PAUSE for their go-ahead. The user sees the plan "
            "with Go / Adjust controls — do not run the steps until they respond.\n\n"
            "Use the structured form when the work is non-trivial: each step is an "
            "object with title (required), optional description, expected_outputs, "
            "skill (the reusable procedure name, if any), and parameters. "
            "For tiny / one-shot plans you may pass `steps` as a list of strings "
            "and they'll be coerced to {title}. Always include `assumptions` for "
            "anything you're taking for granted (defaults, modality, etc.) — the "
            "user can correct them before Go."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title for the plan."},
                "summary": {
                    "type": "string",
                    "description": "One-line synopsis of what the plan produces.",
                },
                "steps": {
                    "type": "array",
                    "description": (
                        "Ordered steps. Each item is preferably an object: "
                        "{title, description?, expected_outputs?, skill?, parameters?}. "
                        "A plain string is accepted and coerced to {title}."
                    ),
                    "items": {
                        "type": ["object", "string"],
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "expected_outputs": {
                                "type": "array", "items": {"type": "string"},
                            },
                            "skill": {
                                "type": "string",
                                "description": (
                                    "Name of a reusable procedure this step invokes "
                                    "(e.g. 'scrna-qc-thresholds'). Leave blank for "
                                    "ad-hoc inline work."
                                ),
                            },
                            "parameters": {"type": "object"},
                        },
                    },
                },
                "assumptions": {
                    "type": "array", "items": {"type": "string"},
                    "description": "What you're taking for granted (defaults, modality, scope).",
                },
                "rationale": {
                    "type": "string",
                    "description": "Optional one-line why / what the user gets.",
                },
            },
            "required": ["steps"],
        },
    },
    {
        "name": "read_memory",
        "description": (
            "Load the body of a named project memory. The system prompt shows a "
            "small index of memories that exist — call this to expand one. "
            "Returns the body, or an error if the name isn't in the index."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Memory name (slug from the index)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "write_memory",
        "description": (
            "Persist a project-local memory the next session should see. Use when "
            "the user states a fact / preference / constraint you'd otherwise have "
            "to re-derive (a control sample id, a domain convention, who's on the "
            "project). Pick the right `type`: user (about the user's role/goals), "
            "feedback (how to work — guidance, corrections), project (the work — "
            "deadlines, decisions, motivations), reference (where to look in "
            "external systems). Overwrite an existing memory by reusing its name. "
            "Don't write what's already in the codebase, git, or another memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string", "description": "Short, kebab-case slug — the index key."},
                "type":        {"type": "string", "enum": ["user", "feedback", "project", "reference"]},
                "description": {"type": "string", "description": "One-line summary for the index."},
                "body":        {"type": "string", "description": "The memory content (markdown)."},
            },
            "required": ["name", "type", "body"],
        },
    },
    {
        "name": "read_skill",
        "description": (
            "Load the full body (procedure / recipe) of a registered skill by name. "
            "The system prompt shows you a one-line description for each skill — call "
            "this when you've decided to use one and need the step-by-step details. "
            "Returns the markdown body, or an error if the name isn't registered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name as shown in the skills index, e.g. 'scrna-qc-clustering'.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "ask_clarification",
        "description": (
            "PAUSE the turn and ask the user ONE specific clarifying question that "
            "you genuinely cannot answer from your loaded context or by inspecting "
            "data. The user's reply resumes this same turn — do not call this for "
            "routine confirmation or plan approval (use present_plan for plan "
            "approval). Good uses: missing modality, ambiguous reference to a "
            "sample, undefined threshold. Bad uses: 'is the data ready?', "
            "'shall I proceed?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "A single, specific question. One sentence. No preamble.",
                },
            },
            "required": ["question"],
        },
    },
]

# ---------- Executors ----------

def list_data_files(_input: dict) -> dict:
    """The project's datasets — the Data facet, NOT the global data folder. Data
    files are stored globally (content-addressed) but each project only *contains*
    the datasets registered as entities in its DB, so we list those."""
    from pathlib import Path as _Path
    from core.graph.entities import list_entities
    files = []
    for e in list_entities(include_archived=False):
        if e.get("type") != "dataset":
            continue
        path = e.get("artifact_path")
        name = _Path(path).name if path else e.get("title", "")
        size = None
        try:
            if path and _Path(path).exists():
                size = _Path(path).stat().st_size
        except Exception:
            pass
        files.append({"filename": name, "size_bytes": size, "title": e.get("title")})
    if not files:
        return {"files": [], "message": "This project has no datasets yet — ask the user to upload one."}
    return {"files": files}


def read_csv_info(input_: dict) -> dict:
    import pandas as pd
    filename = input_.get("filename", "")
    path = DATA_DIR / filename
    if not path.exists():
        return {"error": f"File not found: {filename}"}
    try:
        df = pd.read_csv(path)
        cols = [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
        preview = df.head(5).to_markdown(index=False)
        return {
            "filename": filename,
            "rows": len(df),
            "columns": len(df.columns),
            "column_info": cols,
            "preview": preview
        }
    except Exception as e:
        return {"error": str(e)}


def run_python(input_: dict) -> dict:
    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 90), 600))
    tmp_dir = Path("/tmp") / f"aba_{uuid.uuid4().hex}"
    tmp_dir.mkdir()
    try:
        # Prepend DATA_DIR injection + make the vendored BioMNI tool library
        # importable in the sandbox (functions are imported, not pre-declared
        # — per aba_arch2.md §5.1). Heavy BioMNI deps may be absent; imports
        # that need them will fail gracefully at use time.
        biomni_path = Path(__file__).parent.parent / "biomni"
        preamble = (
            f"DATA_DIR = {str(DATA_DIR)!r}\n"
            f"import sys as _sys\n"
            f"_sys.path.insert(0, {str(biomni_path)!r})\n"
        )
        full_code = preamble + code
        script = tmp_dir / "script.py"
        script.write_text(full_code)

        import os
        env = os.environ.copy()
        env["MPLBACKEND"] = "Agg"
        # Use the same python that's running this server (keeps venv libs available)
        python = sys.executable

        result = subprocess.run(
            [python, str(script)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            cwd=str(tmp_dir)
        )

        # Collect any PNG files produced
        plots = []
        for png in tmp_dir.glob("*.png"):
            dest_name = f"{uuid.uuid4().hex}.png"
            dest = ARTIFACTS_DIR / dest_name
            shutil.move(str(png), str(dest))
            plots.append({"url": f"/artifacts/{dest_name}", "original_name": png.name})

        # Collect any CSV files produced (output tables).
        tables = []
        for csv in tmp_dir.glob("*.csv"):
            dest_name = f"{uuid.uuid4().hex}.csv"
            dest = ARTIFACTS_DIR / dest_name
            shutil.move(str(csv), str(dest))
            tables.append({"url": f"/artifacts/{dest_name}", "original_name": csv.name})

        return {
            "stdout": result.stdout[:4000] if result.stdout else "",
            "stderr": result.stderr[:2000] if result.stderr else "",
            "returncode": result.returncode,
            "plots": plots,
            "tables": tables,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Code execution timed out ({timeout_s}s limit)"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def inspect_upload(input_: dict) -> dict:
    """
    Inspect a file or directory under DATA_DIR. Auto-extracts archives.
    Returns:
      {
        "root": "<resolved path relative to DATA_DIR>",
        "kind": "file" | "directory" | "archive",
        "extracted_to": "<dir>",          # only when archive
        "files": [{"path": ..., "size": ..., "type": ...}, ...],
        "suggested_loader": "<text>",
        "summary": "<one line description>",
      }
    """
    import tarfile
    import zipfile
    raw = input_.get("path", "")
    if not raw:
        return {"error": "path is required"}
    p = Path(raw)
    if not p.is_absolute():
        p = DATA_DIR / p
    try:
        p = p.resolve()
    except FileNotFoundError:
        return {"error": f"path not found: {raw}"}
    if not str(p).startswith(str(DATA_DIR.resolve())):
        return {"error": "path is outside DATA_DIR"}
    if not p.exists():
        return {"error": f"path not found: {raw}"}

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
        return _describe_directory(ext_dir, kind="archive", extracted_to=str(ext_dir),
                                   original_path=str(p))

    if p.is_dir():
        return _describe_directory(p, kind="directory")

    # Single file.
    return {
        "root": str(p),
        "kind": "file",
        "files": [_describe_file(p)],
        "suggested_loader": _suggest_single_loader(p),
        "summary": f"single file: {p.name} ({_fmt_size(p.stat().st_size)})",
    }


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


def get_provenance(input_: dict) -> dict:
    from core.graph.provenance import provenance_text, neighborhood
    eid = input_.get("entity_id", "")
    return {"text": provenance_text(eid), "graph": neighborhood(eid)["upstream"]}


def get_dependents(input_: dict) -> dict:
    from core.graph.provenance import dependents_text, neighborhood
    eid = input_.get("entity_id", "")
    return {"text": dependents_text(eid), "graph": neighborhood(eid)["downstream"]}


def create_scenario(input_: dict) -> dict:
    from content.bio.lifecycle.scenarios import create_scenario_variant
    from core.graph.provenance import downstream
    try:
        variant = create_scenario_variant(
            baseline_id=input_.get("baseline_id", ""),
            description=input_.get("description", ""),
            code=input_.get("code"),
        )
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}
    # Surface baseline dependents the user may want to revisit under the scenario.
    dependents = downstream(input_.get("baseline_id", ""))
    review = [d for d in dependents if d["type"] in ("result", "finding", "claim")]
    return {
        "scenario": {"id": variant["id"], "title": variant["title"]},
        "dependents_to_review": [
            {"id": d["id"], "type": d["type"], "title": d["title"]} for d in review
        ],
        "note": (
            "Scenario created. " + (
                f"{len(review)} downstream "
                f"{'entity references' if len(review)==1 else 'entities reference'} "
                f"the baseline — consider whether they still hold under this "
                f"scenario." if review else "No downstream results to review."
            )
        ),
    }


def present_plan(input_: dict) -> dict:
    """No-op server-side: the plan is surfaced to the UI and the turn halts in
    guide.py. The result just acknowledges so the conversation stays well-formed."""
    return {"status": "presented",
            "note": "Plan shown to the user with Go / Adjust controls. Stop here and "
                    "wait for their decision before executing the steps."}


def read_memory_tool(input_: dict) -> dict:
    from core.memory import read_memory as _rm, list_memories
    name = (input_.get("name") or "").strip() if isinstance(input_, dict) else ""
    if not name:
        return {"status": "error", "note": "read_memory needs a non-empty `name`."}
    e = _rm(name)
    if e is None:
        avail = [m.name for m in list_memories()]
        return {
            "status": "unknown_memory",
            "note": f"No memory named {name!r}. Available: {', '.join(avail) or '(none)'}.",
        }
    return {
        "status": "ok",
        "name": e.name,
        "type": e.type,
        "description": e.description,
        "body": e.body,
    }


def write_memory_tool(input_: dict) -> dict:
    from core.memory import write_memory as _wm, MEMORY_TYPES
    if not isinstance(input_, dict):
        return {"status": "error", "note": "write_memory needs an object input."}
    name = (input_.get("name") or "").strip()
    body = input_.get("body") or ""
    typ  = (input_.get("type") or "").strip()
    desc = (input_.get("description") or "").strip()
    if not name:
        return {"status": "error", "note": "write_memory needs `name`."}
    if not body.strip():
        return {"status": "error", "note": "write_memory needs `body`."}
    if typ not in MEMORY_TYPES:
        return {"status": "error",
                "note": f"`type` must be one of {list(MEMORY_TYPES)}; got {typ!r}."}
    try:
        e = _wm(name=name, body=body, type=typ, description=desc)
    except Exception as ex:  # noqa: BLE001
        return {"status": "error", "note": str(ex)}
    return {"status": "ok", "name": e.name, "type": e.type, "description": e.description}


def read_skill(input_: dict, ctx: dict | None = None) -> dict:
    """Return the body of a registered skill, or an error if absent.

    #5 — Skill-to-tool linkage: if the skill declares `requires_tools` in
    its frontmatter and any of those aren't currently active for this
    turn, return a structured error so the model knows to either pick a
    different approach or ask the user to enable the missing tools.
    Linkage check is skipped when ctx is absent (legacy callers / tests
    without dispatch ctx).
    """
    from core.skills import get_skill
    name = (input_.get("name") or "").strip() if isinstance(input_, dict) else ""
    if not name:
        return {"status": "error", "note": "read_skill needs a non-empty `name`."}
    spec = get_skill(name)
    if spec is None:
        from core.skills import list_skills
        avail = [s.name for s in list_skills()]
        return {
            "status": "unknown_skill",
            "note": f"No skill named {name!r}. Available: {', '.join(avail) or '(none)'}.",
        }

    # #5 — surface missing required tools BEFORE returning the body. The
    # model gets a clear "you can't use this skill as-is" signal instead
    # of reading the body and then having a tool call fail.
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

    return {
        "status": "ok",
        "name": spec.name,
        "description": spec.description,
        "when_to_use": spec.when_to_use,
        "requires_tools": list(spec.requires_tools),
        "produces": list(spec.produces),
        "body": spec.body,
    }


def ask_clarification(input_: dict) -> dict:
    """No-op server-side, like present_plan. The actual halt + SSE emission
    happens in guide.py's tool-dispatch branch; this stub exists so
    EXECUTORS.get('ask_clarification') doesn't fall through to 'Unknown tool'
    if the dispatch order ever changes."""
    return {"status": "asked",
            "note": "Question shown to the user. Stop here and wait for "
                    "their reply before continuing."}


def _test_deferred_tool(input_: dict) -> dict:
    """P2 #4 test scaffolding only — not registered in TOOL_SCHEMAS. A
    test reaches in to add it to EXECUTORS, calls it, then removes it.
    Returns the deferred shape the guide loop recognizes."""
    return {"deferred": True, "deferred_id": input_.get("id") or "demo-job-1"}


EXECUTORS = {
    "list_data_files": list_data_files,
    "read_csv_info": read_csv_info,
    "run_python": run_python,
    "inspect_upload": inspect_upload,
    "get_provenance": get_provenance,
    "get_dependents": get_dependents,
    "create_scenario": create_scenario,
    "present_plan": present_plan,
    "ask_clarification": ask_clarification,
    "read_skill": read_skill,
    "read_memory": read_memory_tool,
    "write_memory": write_memory_tool,
}

def execute_tool(name: str, input_: dict, ctx: dict | None = None) -> str:
    """Dispatch a tool call. `ctx` is optional per-turn context that a few
    tools consult (read_skill uses ctx['active_tools'] to enforce
    skill-tool linkage). Most executors ignore it."""
    import inspect
    fn = EXECUTORS.get(name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        # Pass ctx only to executors that declare it; preserves the
        # 1-arg signature for everyone else.
        sig_params = inspect.signature(fn).parameters
        result = fn(input_, ctx) if "ctx" in sig_params else fn(input_)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})
