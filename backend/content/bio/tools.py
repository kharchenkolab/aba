import signal
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
            "explicitly (max 1800s). Long runs (or background=true) are routed "
            "to a background job so the conversation isn't blocked."
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
                    "description": "Optional timeout in seconds; capped at 1800. Large values auto-route to a background job.",
                    "minimum": 5,
                    "maximum": 1800,
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
    {
        "name": "list_capabilities",
        "description": (
            "Search the capability catalog — the tools and libraries available "
            "(or installable on demand) for analysis. Use this when you need a "
            "tool you're not sure is installed (e.g. enrichment, a specific "
            "parser, a quantifier). Returns name, what it does, and whether it's "
            "a Python library or a CLI tool. Pair with ensure_capability to make "
            "one ready before using it in run_python."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Free-text search over names/summaries/tags, e.g. 'enrichment'."},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "Optional domain tags to filter by, e.g. ['rna-seq']."},
            },
        },
    },
    {
        "name": "ensure_capability",
        "description": (
            "Make a catalogued capability ready to use, materializing it on "
            "demand if needed. For a Python library this installs it into the "
            "materialized-library overlay so the very next run_python can import "
            "it. Call this BEFORE run_python when your code needs a package that "
            "isn't in the base environment. Returns status 'ready' (importable "
            "now), 'deferred' (a CLI tool whose install path isn't wired yet), "
            "or 'not_found'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Capability name from the catalog (e.g. 'gseapy')."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_pypi",
        "description": (
            "Look up a Python package on PyPI when a library you need isn't in "
            "the catalog (list_capabilities missed it). Returns whether it "
            "exists plus version/summary. Follow with propose_capability to add "
            "it. For non-Python CLI tools (aligners, QC binaries) use "
            "search_bioconda instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Package name, e.g. 'umap-learn'."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_bioconda",
        "description": (
            "Check whether a command-line bioinformatics tool exists on bioconda "
            "(e.g. bowtie2, bedtools, samtools). If found, it's installable on "
            "demand: propose_capability(name, archetype='cli') then "
            "ensure_capability puts it on PATH for run_python. For Python "
            "libraries use search_pypi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Tool name, e.g. 'salmon'."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "propose_capability",
        "description": (
            "Add a tool to the catalog on demand. For a Python library (default, "
            "archetype='library') found via search_pypi; for a command-line tool "
            "(archetype='cli') found via search_bioconda. In solo mode it's "
            "auto-approved and ready to install via ensure_capability. For a "
            "library whose import name differs from the package name, pass "
            "import_name (e.g. 'scikit-image' imports as 'skimage')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Package/tool name."},
                "archetype": {"type": "string", "enum": ["library", "cli"],
                              "description": "'library' = Python package (pip); 'cli' = command-line tool (conda)."},
                "channel": {"type": "string",
                            "description": "Conda channel for cli tools (default 'bioconda')."},
                "version": {"type": "string", "description": "Optional pinned version."},
                "summary": {"type": "string", "description": "Optional one-line description."},
                "import_name": {"type": "string",
                                "description": "For a library: Python import name, if it differs from the package name."},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "Optional domain tags."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Download a file from a URL into the project's fetch workspace "
            "(scratch). Use for public data — a genome/annotation file, a fastq "
            "URL from lookup_sra_runinfo, etc. Returns the local path; then call "
            "register_reference to keep reusable reference data, or just read it "
            "from run_python. Large downloads are size-gated + audited."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "filename": {"type": "string", "description": "Optional output filename."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "lookup_sra_runinfo",
        "description": (
            "Look up the run table for an SRA/ENA/GEO accession (study, sample, "
            "or run) via ENA. Returns each run's accession, sample title, library "
            "layout, and direct fastq download URLs — the input for planning a "
            "fetch+align pipeline. Pair with fetch_url to download the fastqs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"accession": {"type": "string",
                            "description": "e.g. 'SRP033351', 'GSE52778', or a run 'SRR1039508'."}},
            "required": ["accession"],
        },
    },
    {
        "name": "fetch_ensembl",
        "description": (
            "Fetch a genome/transcriptome FASTA or GTF annotation from Ensembl. "
            "Resolves the assembly-versioned filename automatically. Use for a "
            "reference you'll align/quantify against; follow with register_reference."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "species": {"type": "string", "description": "e.g. 'drosophila_melanogaster'."},
                "kind": {"type": "string", "enum": ["cdna", "dna", "gtf"],
                         "description": "cdna (transcriptome), dna (genome), or gtf (annotation)."},
                "release": {"type": "string", "description": "Ensembl release, default '110'."},
            },
            "required": ["species", "kind"],
        },
    },
    {
        "name": "register_reference",
        "description": (
            "Keep a fetched/built file as a reusable reference in the shared, "
            "content-addressed store (deduplicated across projects). Tag it with "
            "organism/role so find_reference can locate it later. For a derived "
            "reference (e.g. an index built from a FASTA), pass derived_from with "
            "the source reference id to record lineage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Local path to the file or index dir."},
                "organism": {"type": "string"},
                "role": {"type": "string", "description": "e.g. 'transcriptome', 'genome', 'salmon_index', 'gtf'."},
                "assembly": {"type": "string"},
                "source": {"type": "string", "description": "Provenance, e.g. 'Ensembl r110'."},
                "derived_from": {"type": "string", "description": "Source reference id, if derived."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_reference",
        "description": (
            "Find an already-stored reference by organism/role (and optionally "
            "assembly) before fetching or building it — references are shared and "
            "deduplicated, so a colleague's fly transcriptome or a previously-built "
            "index is reused, not re-fetched. Pass all=true to list matches."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "organism": {"type": "string"},
                "role": {"type": "string"},
                "assembly": {"type": "string"},
                "all": {"type": "boolean", "description": "Return all matches instead of the first."},
            },
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


def run_python(input_: dict, ctx: dict | None = None) -> dict:
    """Run Python in the project's scratch workspace via the shared executor.

    P0 (data.md / capdat_impl.md): the run executes in a per-run scratch dir
    under WORK_DIR (the agent reads/writes intermediates there freely, by plain
    path) and goes through LocalSubprocessExecutor so the exec + cancellation +
    timeout contract is shared with future executors. Kept outputs (*.png/*.csv)
    are still moved to the content-addressed artifact store and returned as
    plots/tables — the on_post_tool registration hook is unchanged. Scratch
    persists across the run's turns and is GC'd on a TTL; it is NOT deleted
    here, so the agent can revisit its working files."""
    from core.exec.run import run_python_code
    from core.exec import LocalRouter
    from core import projects

    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 90), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    project_id = projects.current() or "default"
    biomni_path = str(Path(__file__).parent.parent / "biomni")

    # P5: decide synchronous vs background. Explicit background flag forces it;
    # otherwise the router auto-routes long runs (timeout_s as the runtime proxy)
    # to the background job queue. Background returns a deferred result the guide
    # loop resumes from when the job completes.
    override = "background" if input_.get("background") else None
    choice = LocalRouter().route(estimate={"runtime_min": timeout_s / 60.0}, override=override)
    if choice.location == "background":
        from core.jobs.runner import submit_python_job
        job = submit_python_job(code, title=input_.get("title") or "Background analysis",
                                focus_entity_id=(ctx or {}).get("focus_entity_id"),
                                timeout_s=timeout_s, project_id=str(project_id))
        return {
            "deferred": True, "deferred_id": job["id"], "job_id": job["id"],
            "status": "submitted",
            "note": f"Submitted as background job {job['id']} ({choice.rationale}). "
                    f"I'll continue when it finishes.",
        }

    run_id = ((ctx or {}).get("run_id")
              or getattr(cancel_token, "run_id", None)
              or uuid.uuid4().hex)
    try:
        return run_python_code(code, project_id=str(project_id), run_id=str(run_id),
                               timeout_s=timeout_s, cancel_token=cancel_token,
                               extra_syspath=[biomni_path])
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


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


def list_capabilities_tool(input_: dict) -> dict:
    """Search the capability catalog (P1). Returns a trimmed view for the model."""
    from core.catalog import list_capabilities as _list
    caps = _list(query=input_.get("query"), tags=input_.get("tags"))
    return {"capabilities": [
        {"name": c.get("name"), "version": c.get("version"),
         "archetype": c.get("archetype"), "summary": c.get("summary"),
         "domain_tags": c.get("domain_tags"), "status": c.get("status")}
        for c in caps
    ]}


def _pep503(name: str) -> str:
    import re
    return re.sub(r"[-_.]+", "-", name).lower()


def search_pypi(input_: dict) -> dict:
    """Look up a Python package on PyPI (P2′ discovery). Resolves the name (and
    PEP-503 / separator variants) against the PyPI JSON API and returns its
    metadata if it exists. Use this when the agent needs a library that
    list_capabilities didn't find, before proposing it."""
    import json as _json
    import urllib.error
    import urllib.request

    raw = (input_.get("query") or input_.get("name") or "").strip()
    if not raw:
        return {"error": "query is required"}
    # Candidate spellings to try, in order; PyPI is case-insensitive and
    # normalizes separators, but trying variants covers user phrasing.
    cands = []
    for c in (raw, _pep503(raw), raw.replace("_", "-"), raw.replace("-", "_")):
        if c and c not in cands:
            cands.append(c)
    for cand in cands:
        try:
            with urllib.request.urlopen(
                f"https://pypi.org/pypi/{cand}/json", timeout=10
            ) as resp:
                info = (_json.loads(resp.read()).get("info") or {})
            return {
                "found": True,
                "name": info.get("name") or cand,
                "version": info.get("version"),
                "summary": info.get("summary"),
                "requires_python": info.get("requires_python"),
                "home_page": info.get("home_page") or info.get("project_url"),
                "tried": cands,
            }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return {"error": f"PyPI lookup failed ({e.code})", "tried": cands}
        except Exception as e:  # noqa: BLE001
            return {"error": f"PyPI lookup failed: {e}", "tried": cands}
    return {"found": False, "tried": cands,
            "note": "No PyPI package by that name. Check spelling, or it may be a "
                    "non-Python CLI tool (try search_bioconda)."}


def search_bioconda(input_: dict) -> dict:
    """Check whether a tool exists on bioconda (P2′ awareness only). Returns
    presence + a note that conda materialization is deferred — so the agent can
    answer honestly about CLI tools it cannot yet install (e.g. salmon, STAR)."""
    import json as _json
    import urllib.error
    import urllib.request

    name = (input_.get("query") or input_.get("name") or "").strip().lower()
    if not name:
        return {"error": "query is required"}
    try:
        with urllib.request.urlopen(
            f"https://api.anaconda.org/package/bioconda/{name}", timeout=10
        ) as resp:
            data = _json.loads(resp.read())
        return {
            "found": True, "name": name,
            "latest_version": data.get("latest_version"),
            "summary": data.get("summary"),
            "note": "Available on bioconda and installable on demand: call "
                    "propose_capability(name, archetype='cli') then ensure_capability — "
                    "it installs into the conda tools env and lands on PATH for run_python.",
        }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"found": False, "name": name}
        return {"error": f"bioconda lookup failed ({e.code})"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"bioconda lookup failed: {e}"}


def ensure_capability(input_: dict) -> dict:
    """Materialize a catalogued capability on demand (P1). Python libraries go
    into the wipeable overlay so the next run_python can import them; non-pip
    CLI tools (conda) are reported as deferred."""
    name = (input_.get("name") or input_.get("capability") or "").strip()
    if not name:
        return {"error": "name is required"}
    from core.catalog import resolve_capability
    cap = resolve_capability(name)
    if not cap:
        return {"status": "not_found",
                "note": f"No capability '{name}' in the catalog. Use list_capabilities to search."}
    # Honor the lifecycle: an unapproved (proposed) capability isn't runnable
    # until approved (the 'ask' multi-user gate).
    if cap.get("status") not in (None, "published"):
        return {"status": "awaiting_approval", "name": cap.get("name"),
                "note": f"'{name}' is proposed but not yet approved; it can't be "
                        f"materialized until approval."}
    prov = cap.get("provisioning") or {}
    if prov.get("pip"):
        from core.exec import MaterializingExecutor, Provisioning
        try:
            MaterializingExecutor().materialize(Provisioning(pip=list(prov["pip"])))
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": name, "note": f"materialization failed: {e}"}
        imp = cap.get("import_name")
        note = "Installed into the materialized-library overlay; importable from run_python now."
        if imp and imp != cap.get("name"):
            note += f" Import it as `{imp}`."
        return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                "archetype": cap.get("archetype"), "import_name": imp, "note": note}
    if prov.get("conda"):
        from core.exec import MaterializingExecutor, Provisioning
        try:
            MaterializingExecutor().materialize(Provisioning(conda=prov["conda"]))
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": name, "note": f"conda materialization failed: {e}"}
        return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                "archetype": cap.get("archetype"),
                "note": "Installed into the conda tools env; the binary is on PATH — "
                        "invoke it from run_python via subprocess."}
    return {"status": "error", "name": name, "note": "capability has no recognized provisioning."}


def propose_capability_tool(input_: dict) -> dict:
    """Add a new Python library to the catalog on demand (P2′ demand loop).
    De-dupes against the existing catalog, then proposes it; in auto-approval
    mode it's published immediately (and audited). Follow with ensure_capability
    to install it. For libraries whose import name differs from the pip name
    (e.g. scikit-image → skimage), pass import_name so the ready note is correct."""
    name = (input_.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    from core.catalog import resolve_capability, propose_capability as _propose, capability_status

    existing = resolve_capability(name)
    if existing:
        return {"status": "already_available", "name": existing.get("name"),
                "version": existing.get("version"),
                "note": "Already in the catalog — call ensure_capability to install it."}

    archetype = (input_.get("archetype") or "library").strip()
    version = str(input_.get("version") or "latest")
    if archetype == "cli":
        # A command-line tool from a conda channel (e.g. bowtie2, bedtools).
        channel = input_.get("channel") or "bioconda"
        conda_spec = f"{name}={version}" if version and version != "latest" else name
        spec = {
            "name": name, "version": version, "archetype": "cli",
            "summary": input_.get("summary") or f"{name} (added on demand from {channel})",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"conda": {"channel": channel, "spec": conda_spec}},
            "source": channel,
        }
    else:
        spec = {
            "name": name, "version": version, "archetype": "library",
            "summary": input_.get("summary") or f"{name} (added on demand from PyPI)",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"pip": [name]},
            "source": "pypi",
        }
        if input_.get("import_name"):
            spec["import_name"] = input_["import_name"]

    cap_id = _propose(spec)
    if capability_status(cap_id) != "published":
        return {"status": "pending_approval", "name": name,
                "note": "Proposed; awaiting approval before it can be installed."}
    if archetype == "cli":
        note = ("Added to the catalog (auto-approved). Call ensure_capability to "
                "install it; the binary will be on PATH — invoke it from run_python via subprocess.")
    else:
        note = ("Added to the catalog (auto-approved). Call ensure_capability to "
                "install it, then import it in run_python.")
    return {"status": "approved", "name": name, "archetype": archetype, "note": note}


def fetch_url(input_: dict, ctx: dict | None = None) -> dict:
    """Download a URL into the project's fetch scratch (P4). Size-gated + audited."""
    import os as _os
    import urllib.request
    from core.data.workspace import scratch_dir
    from core.graph.audit import log_event
    from core import projects

    url = (input_.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    filename = input_.get("filename") or url.split("?")[0].rstrip("/").split("/")[-1] or "download"
    project_id = projects.current() or "default"
    dest = scratch_dir(str(project_id), "fetch") / filename
    threshold = 5 * 1024 ** 3
    mode = _os.environ.get("ABA_CAPABILITY_APPROVAL", "auto")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as resp:
            clen = int(resp.headers.get("Content-Length") or 0)
            if clen > threshold and mode == "ask":
                return {"status": "needs_approval", "url": url, "bytes": clen,
                        "note": f"Download is ~{clen} bytes (over threshold); approval required in ask mode."}
            total = 0
            with open(dest, "wb") as f:
                for chunk in iter(lambda: resp.read(1 << 20), b""):
                    f.write(chunk)
                    total += len(chunk)
    except Exception as e:  # noqa: BLE001
        return {"error": f"fetch failed: {e}"}
    log_event("data_fetched", title=filename, detail={"url": url, "bytes": total, "path": str(dest)})
    return {"status": "ok", "path": str(dest), "filename": filename, "bytes": total}


def lookup_sra_runinfo(input_: dict, ctx: dict | None = None) -> dict:
    """Run table for an SRA/ENA/GEO accession via the ENA filereport API (P4)."""
    import json as _json
    import urllib.request
    acc = (input_.get("accession") or input_.get("query") or "").strip()
    if not acc:
        return {"error": "accession is required"}
    fields = "run_accession,fastq_ftp,sample_title,sample_accession,library_layout,read_count"
    url = (f"https://www.ebi.ac.uk/ena/portal/api/filereport?accession={acc}"
           f"&result=read_run&fields={fields}&format=json")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = _json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        return {"error": f"ENA lookup failed: {e}"}
    runs = []
    for r in data:
        urls = [(u if u.startswith("http") else "https://" + u)
                for u in (r.get("fastq_ftp") or "").split(";") if u]
        runs.append({"run_accession": r.get("run_accession"),
                     "sample_title": r.get("sample_title"),
                     "library_layout": r.get("library_layout"),
                     "read_count": r.get("read_count"),
                     "fastq_urls": urls})
    return {"accession": acc, "n_runs": len(runs), "runs": runs}


def fetch_ensembl(input_: dict, ctx: dict | None = None) -> dict:
    """Fetch a FASTA/GTF from Ensembl, resolving the assembly-versioned filename
    by listing the release directory (P4)."""
    import re
    import urllib.request
    species = (input_.get("species") or "").strip().lower()
    kind = (input_.get("kind") or "cdna").strip()
    release = str(input_.get("release") or "110")
    if not species:
        return {"error": "species is required"}
    if kind in ("cdna", "dna"):
        dir_url = f"https://ftp.ensembl.org/pub/release-{release}/fasta/{species}/{kind}/"
        suffix = ".cdna.all.fa.gz" if kind == "cdna" else ".dna.toplevel.fa.gz"
    elif kind == "gtf":
        dir_url = f"https://ftp.ensembl.org/pub/release-{release}/gtf/{species}/"
        suffix = f".{release}.gtf.gz"
    else:
        return {"error": f"unknown kind '{kind}'"}
    try:
        with urllib.request.urlopen(dir_url, timeout=30) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return {"error": f"Ensembl listing failed: {e}", "dir": dir_url}
    files = re.findall(r'href="([^"]+)"', html)
    match = next((f for f in files if f.endswith(suffix)), None)
    if not match:
        return {"error": f"no '{suffix}' file in {dir_url}", "candidates": files[:20]}
    return fetch_url({"url": dir_url + match, "filename": match}, ctx)


def register_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Keep a file/dir as a reusable, content-addressed reference (P4)."""
    path = input_.get("path")
    if not path:
        return {"error": "path is required"}
    from core.data import register_reference as _reg
    from core.graph.entities import get_entity
    try:
        eid = _reg(path, organism=input_.get("organism"), role=input_.get("role"),
                   source=input_.get("source"), assembly=input_.get("assembly"),
                   derived_from=input_.get("derived_from"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"register failed: {e}"}
    e = get_entity(eid) or {}
    meta = e.get("metadata") or {}
    return {"status": "ok", "reference_id": eid, "sha": meta.get("sha"),
            "organism": meta.get("organism"), "role": meta.get("role"),
            "artifact_path": e.get("artifact_path"),
            "note": "Stored content-addressed (deduplicated). Reuse via find_reference."}


def find_reference_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Find a stored reference by organism/role before fetching/building (P4)."""
    from core.data import find_reference as _find, list_references as _list
    if input_.get("all"):
        return {"references": _list(organism=input_.get("organism"), role=input_.get("role"),
                                    assembly=input_.get("assembly"))}
    r = _find(organism=input_.get("organism"), role=input_.get("role"),
              assembly=input_.get("assembly"))
    return {"found": bool(r), "reference": r}


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
    "list_capabilities": list_capabilities_tool,
    "ensure_capability": ensure_capability,
    "search_pypi": search_pypi,
    "search_bioconda": search_bioconda,
    "propose_capability": propose_capability_tool,
    "fetch_url": fetch_url,
    "lookup_sra_runinfo": lookup_sra_runinfo,
    "fetch_ensembl": fetch_ensembl,
    "register_reference": register_reference_tool,
    "find_reference": find_reference_tool,
}

def execute_tool(name: str, input_: dict, ctx: dict | None = None) -> str:
    """Dispatch a tool call. `ctx` is optional per-turn context that a few
    tools consult (read_skill uses ctx['active_tools'] to enforce
    skill-tool linkage). Most executors ignore it.

    Falls through to the MCP gateway for tools the gateway owns
    (prefixed 'server:name'); returns the gateway's result dict
    serialized back to a string."""
    import inspect
    fn = EXECUTORS.get(name)
    if fn is None:
        # P3 #1 — try the MCP gateway. Tool names there are 'server:tool'.
        # Forward the cancel token so a Stop click can interrupt the call.
        try:
            from core.runtime.mcp import is_mcp_tool, call as mcp_call
            if is_mcp_tool(name):
                cancel_token = (ctx or {}).get("cancel_token")
                return json.dumps(mcp_call(name, input_ or {}, cancel_token=cancel_token))
        except Exception:  # noqa: BLE001
            pass    # fall through to unknown-tool error
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        sig_params = inspect.signature(fn).parameters
        result = fn(input_, ctx) if "ctx" in sig_params else fn(input_)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})
