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
            "Execute Python in a PERSISTENT session for this investigation — like "
            "a notebook. Variables, imports, and loaded data PERSIST across "
            "run_python calls, so load files and compute expensive things (e.g. a "
            "DESeq2 fit) ONCE and reuse them; do not re-read inputs or refit models "
            "you already have in memory. The session can be reset (idle timeout or "
            "via restart_kernel), which clears state — so save important results to "
            "disk (to_parquet / np.save) and reload them rather than relying on "
            "memory for anything costly to recompute. "
            "pandas, numpy, matplotlib, scanpy, anndata are available. The data "
            "folder is the DATA_DIR variable, ALREADY DEFINED in your session — "
            "reference files as DATA_DIR/<name> (e.g. pd.read_csv(f'{DATA_DIR}/counts.csv')); "
            "never hardcode paths like /project/data, and call list_data_files if "
            "unsure what's there. Save plots as plt.savefig('out.png') — they're "
            "captured. Set fresh=true for a one-off ISOLATED run that neither reads "
            "nor changes the session (use for reproducible/self-contained code). "
            "Set background=true for a long pipeline that shouldn't block the chat. "
            "timeout_s caps a run (max 1800s)."
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
                    "description": "Hard time LIMIT (ceiling) in seconds; capped at 1800. The run is killed if it exceeds this. This is NOT a runtime estimate and does not affect background routing — set it generously.",
                    "minimum": 5,
                    "maximum": 1800,
                },
                "background": {
                    "type": "boolean",
                    "description": "Run as a background job instead of inline. Use for long pipelines (fetch+align, multi-sample quantification) so the conversation isn't blocked. Returns a job_id immediately; figures register when the job finishes. Tell the user to watch the Queues panel.",
                },
                "estimated_runtime_min": {
                    "type": "number",
                    "description": "Optional: your estimate of how long this will take, in minutes. If it exceeds the background threshold (~4 min) the run is auto-routed to a background job. Leave unset for quick steps.",
                },
                "fresh": {
                    "type": "boolean",
                    "description": "Run one-off in a clean, isolated process instead of the persistent session — nothing from the session is available and nothing persists. Use for self-contained/reproducible code or a quick isolated check.",
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
        "name": "run_r",
        "description": (
            "Execute R in a PERSISTENT R session for this investigation (like an R "
            "notebook). Objects — a SummarizedExperiment, DESeqDataSet, Seurat "
            "object — PERSIST across run_r calls, so build them once and reuse. "
            "Bioconductor/CRAN packages added via ensure_capability (conda) are "
            "available. Hand data between Python and R by writing files to "
            "DATA_DIR / the working dir (CSV / Parquet / RDS) — the R and Python "
            "sessions for this thread share that directory. DATA_DIR is defined. "
            "Use for Bioconductor / DESeq2 / edgeR / limma / Seurat work that's "
            "awkward in Python. First R use installs the R kernel (slow, one-time)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "R code to execute."},
                "timeout_s": {"type": "integer", "minimum": 5, "maximum": 1800,
                              "description": "Hard time limit; default 120s."},
            },
            "required": ["code"],
        },
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
            "Returns the markdown body plus the capabilities the skill needs, or an "
            "error if the name isn't registered."
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
        "name": "search_skills",
        "description": (
            "Find skills (reusable analysis recipes) by intent when the one you "
            "need isn't in the skills list shown in your prompt — that list is only "
            "a relevant slice of a larger library. Search by what you want to do "
            "('differential expression', 'cluster single-cell data', 'call "
            "variants'), not by exact name. Returns ranked skills with their "
            "descriptions and the capabilities each needs; follow with read_skill "
            "to load one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What you want to do, in plain words."},
                "limit": {"type": "integer",
                          "description": "Max results (default 8)."},
            },
            "required": ["query"],
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
        "name": "read_capability",
        "description": (
            "Get full detail for one capability by name — what it does, its inputs "
            "(params), and, for a reference entry (e.g. a tool mined from biomni), "
            "where the original implementation lives (source_ref). The catalogue "
            "search returns trimmed rows; call this once you've picked a candidate "
            "and need its signature before using or implementing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Capability name from the catalogue."},
            },
            "required": ["name"],
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
        "name": "search_nf_core",
        "description": (
            "Discover nf-core pipelines by intent (e.g. 'rna-seq quantification', "
            "'variant calling', 'methylation') when the analysis is a whole curated "
            "workflow rather than a single tool. Returns ranked pipelines; adopt one "
            "with propose_capability(archetype='pipeline'), ensure_capability to "
            "install nextflow, then run_nextflow to execute it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the pipeline should do, in plain words."},
                "limit": {"type": "integer", "description": "Max results (default 8)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_nextflow",
        "description": (
            "Run a Nextflow / nf-core pipeline (e.g. 'nf-core/rnaseq', or "
            "'nextflow-io/hello' to smoke-test). Installs nextflow on demand, runs "
            "`nextflow run <pipeline>` in the project workspace, and returns the log + "
            "output files. Use profile='test' for a quick canned run; pass pipeline "
            "params via `params` (e.g. {input: samplesheet.csv, genome: GRCh38}). "
            "Local execution only for now — large runs will move to HPC/remote later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline": {"type": "string",
                             "description": "Pipeline ID, e.g. 'nf-core/rnaseq' or 'nextflow-io/hello'."},
                "revision": {"type": "string", "description": "Pipeline revision/version (-r), optional."},
                "profile": {"type": "string",
                            "description": "Nextflow profile, e.g. 'test', 'docker', 'test,docker'."},
                "params": {"type": "object",
                           "description": "Pipeline --params as a flat object (key -> value)."},
                "outdir": {"type": "string", "description": "Output directory (default: a scratch results dir)."},
                "timeout_s": {"type": "integer", "description": "Max seconds (default 1800, cap 3600)."},
            },
            "required": ["pipeline"],
        },
    },
    {
        "name": "search_mcp_registry",
        "description": (
            "Discover external MCP servers (tool servers published by others) by "
            "intent when no in-catalog capability fits. Returns ranked servers with "
            "a connection hint; adopt one with propose_capability(archetype="
            "'mcp_server', connection=...), then ensure_capability connects it live "
            "and its tools become callable as 'server:tool' this session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What capability you need, in plain words."},
                "limit": {"type": "integer", "description": "Max results (default 8)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "propose_capability",
        "description": (
            "Add a tool to the catalog on demand. Archetypes: 'library' (Python "
            "package via pip, found via search_pypi); 'cli' (command-line tool via "
            "conda, found via search_bioconda); 'mcp_server' (external MCP server "
            "found via search_mcp_registry — pass connection={command,args} or "
            "{transport,url}); 'pipeline' (nf-core pipeline found via search_nf_core). "
            "In solo mode it's auto-approved. For a library whose import name differs "
            "from the package name, pass import_name (e.g. 'scikit-image' → 'skimage')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Package/tool/server/pipeline name."},
                "archetype": {"type": "string", "enum": ["library", "cli", "mcp_server", "pipeline"],
                              "description": "'library'=pip; 'cli'=conda; 'mcp_server'=external MCP server; 'pipeline'=nf-core."},
                "channel": {"type": "string",
                            "description": "Conda channel for cli tools (default 'bioconda')."},
                "connection": {"type": "object",
                               "description": "For mcp_server: {command, args[], env{}} (stdio) or {transport, url} (remote)."},
                "url": {"type": "string", "description": "For pipeline: the nf-core URL."},
                "revision": {"type": "string", "description": "For pipeline: pinned revision/version."},
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
        "name": "restart_kernel",
        "description": (
            "Clear this investigation's persistent Python session — all variables, "
            "imports, and loaded data are reset, and the next run_python starts "
            "fresh. Use when the session state is confused/corrupted, or when you "
            "deliberately want a clean slate. Does not delete files on disk."
        ),
        "input_schema": {"type": "object", "properties": {}},
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
    registered_names = set()
    for e in list_entities(include_archived=False):
        if e.get("type") != "dataset":
            continue
        path = e.get("artifact_path")
        name = _Path(path).name if path else e.get("title", "")
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
                      "title": e.get("title"), "registered": True})

    # Also surface data files sitting in DATA_DIR that aren't registered as
    # datasets — otherwise the agent sees "no datasets", concludes the project
    # is empty, and asks the user to upload files that are already present.
    # These are readable directly by filename (read_csv_info / run_python).
    _DATA_EXTS = {".csv", ".tsv", ".tab", ".txt", ".xlsx", ".parquet",
                  ".h5ad", ".h5", ".loom", ".mtx", ".gz", ".tar", ".zip", ".fa", ".fasta"}
    n_unregistered = 0
    try:
        for p in sorted(DATA_DIR.iterdir()):
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
    # Always tell the agent HOW to load them — the absolute path + the DATA_DIR
    # convention — so it doesn't guess paths like /project/data.
    out = {"files": files,
           "data_dir": str(DATA_DIR),
           "message": ("Load these via the DATA_DIR variable (already defined in run_python): "
                       "e.g. pd.read_csv(f'{DATA_DIR}/<filename>'). Use the listed `path` "
                       "values directly — do not hardcode other directories.")}
    return out


def read_csv_info(input_: dict) -> dict:
    import pandas as pd
    filename = input_.get("filename", "")
    path = DATA_DIR / filename
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
    import time as _time
    from core.exec.run import run_python_code, harvest_artifacts
    from core.exec import LocalRouter
    from core.config import KERNEL_ENABLED
    from core import projects

    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 90), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    project_id = projects.current() or "default"
    thread_id = (ctx or {}).get("thread_id") or "default"

    # Lane selection (kernels.md §7): background > fresh > interactive.
    # - background: stateless job, deferred result the guide loop resumes from.
    # - fresh: stateless one-shot subprocess (isolated/reproducible; no session).
    # - interactive (default): the thread's persistent kernel (state persists).
    # timeout_s is a CEILING, not an estimate; routing to background keys on the
    # agent's estimated_runtime_min so a defensive timeout doesn't mis-background.
    override = "background" if input_.get("background") else None
    est_min = float(input_.get("estimated_runtime_min") or 0)
    choice = LocalRouter().route(estimate={"runtime_min": est_min}, override=override)
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

    # Interactive persistent kernel — the default. State persists across calls
    # within this thread, so the agent reuses loaded data / fitted models.
    if KERNEL_ENABLED and not input_.get("fresh"):
        try:
            from core.exec.kernels import get_pool
            from core.data.workspace import scratch_dir
            cwd = scratch_dir(str(project_id), f"thread-{thread_id}")   # persistent per thread
            start_ts = _time.time()
            sess = get_pool().get_or_start(str(thread_id), "python", cwd=str(cwd))
            res = sess.execute(code, cancel_token=cancel_token, timeout_s=timeout_s)
            if res.timed_out:
                return {"error": f"Code execution timed out ({timeout_s}s limit)"}
            if res.cancelled:
                return {"status": "cancelled",
                        "note": f"Run was cancelled by the user "
                                f"({getattr(cancel_token, 'reason', '')}). No further work happened."}
            plots, tables = harvest_artifacts(cwd, since_ts=start_ts)
            # Session-derived: reproduction needs this thread's ordered cells,
            # not the single cell alone (kernels.md §8.1).
            return {"stdout": (res.stdout or "")[:4000], "stderr": (res.stderr or "")[:2000],
                    "returncode": res.returncode, "plots": plots, "tables": tables,
                    "execution_mode": "session"}
        except Exception as e:  # noqa: BLE001
            # Never strand the agent on a kernel hiccup — fall back to stateless.
            print(f"[run_python] kernel path failed, falling back to one-shot: {e}")

    # Stateless one-shot (fresh=true, kernel disabled, or kernel fallback).
    run_id = ((ctx or {}).get("run_id")
              or getattr(cancel_token, "run_id", None)
              or uuid.uuid4().hex)
    try:
        return run_python_code(code, project_id=str(project_id), run_id=str(run_id),
                               timeout_s=timeout_s, cancel_token=cancel_token,
                               extra_syspath=[])
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def run_r(input_: dict, ctx: dict | None = None) -> dict:
    """Execute R in the thread's persistent R (IRkernel) session — objects
    persist across calls, and the session shares the thread's working dir with
    run_python for file handoff (CSV/Parquet/RDS). For Bioconductor/DESeq2/
    edgeR/limma/Seurat work."""
    import time as _time
    from core.exec.run import harvest_artifacts
    from core.config import KERNEL_ENABLED
    from core import projects

    if not KERNEL_ENABLED:
        return {"error": "R runs in a persistent kernel, which is currently disabled."}
    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 120), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    project_id = projects.current() or "default"
    thread_id = (ctx or {}).get("thread_id") or "default"
    try:
        from core.exec.kernels import get_pool
        from core.data.workspace import scratch_dir
        cwd = scratch_dir(str(project_id), f"thread-{thread_id}")   # shared with the Python kernel
        start_ts = _time.time()
        sess = get_pool().get_or_start(str(thread_id), "r", cwd=str(cwd))
        res = sess.execute(code, cancel_token=cancel_token, timeout_s=timeout_s)
    except Exception as e:  # noqa: BLE001
        return {"error": f"R kernel error: {e}"}
    if res.timed_out:
        return {"error": f"R code timed out ({timeout_s}s limit)"}
    if res.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}
    plots, tables = harvest_artifacts(cwd, since_ts=start_ts)
    return {"stdout": (res.stdout or "")[:4000], "stderr": (res.stderr or "")[:2000],
            "returncode": res.returncode, "plots": plots, "tables": tables,
            "execution_mode": "session"}


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

    # Skill→capability funnel: the skill names the catalog capabilities it
    # uses; tell the agent which aren't ready yet so it can ensure_capability
    # them before run_python (rather than hitting an ImportError mid-run).
    out = {
        "status": "ok",
        "name": spec.name,
        "description": spec.description,
        "when_to_use": spec.when_to_use,
        "requires_tools": list(spec.requires_tools),
        "capabilities_needed": list(spec.capabilities_needed),
        "produces": list(spec.produces),
        "body": spec.body,
    }
    if spec.capabilities_needed:
        out["note"] = (
            "This skill uses these capabilities: "
            f"{', '.join(spec.capabilities_needed)}. "
            "Call ensure_capability(name) for any not already available before run_python."
        )
    return out


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


def read_capability(input_: dict) -> dict:
    """Full detail for one capability by name — what it does, its inputs, and
    (for a reference entry like a biomni tool) where the implementation lives.
    Mirrors read_skill: list/search stay trimmed; this expands one on demand."""
    name = (input_.get("name") or input_.get("capability") or "").strip()
    if not name:
        return {"status": "error", "note": "read_capability needs a non-empty `name`."}
    from core.catalog import resolve_capability
    cap = resolve_capability(name)
    if not cap:
        return {"status": "not_found",
                "note": f"No capability '{name}'. Use list_capabilities to search."}
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
    else:
        if cap.get("version"):
            out["version"] = cap.get("version")
        if cap.get("import_path"):
            out["import_path"] = cap.get("import_path")
        out["note"] = "Use ensure_capability to make it ready, then use it in run_python."
    return out


def list_capabilities_tool(input_: dict) -> dict:
    """Search the capability catalog (P1). Intent-ranked (BM25 + substring)
    when a query is given, plain tag-filter otherwise. Returns a trimmed
    view for the model."""
    query = input_.get("query")
    tags = input_.get("tags")
    if (query or "").strip():
        from core.catalog import search_capabilities as _search
        caps = _search(query=query, tags=tags)
    else:
        from core.catalog import list_capabilities as _list
        caps = _list(query=None, tags=tags)
    return {"capabilities": [
        {"name": c.get("name"), "version": c.get("version"),
         "archetype": c.get("archetype"), "summary": c.get("summary"),
         "domain_tags": c.get("domain_tags"), "status": c.get("status")}
        for c in caps
    ]}


def search_skills_tool(input_: dict) -> dict:
    """Intent search over the skill (recipe) library. The system prompt only
    surfaces a relevant slice of skills; this finds the rest by free-text
    intent ('differential expression', 'cluster single cell data') so the
    agent isn't limited to what happened to be in-prompt this turn."""
    from core.skills import search_skills
    q = (input_.get("query") or "").strip()
    if not q:
        return {"status": "error", "note": "search_skills needs a non-empty `query`."}
    limit = input_.get("limit") or 8
    hits = search_skills(q, limit=int(limit))
    return {"skills": [
        {"name": s.name, "description": s.description,
         "when_to_use": s.when_to_use,
         "capabilities_needed": list(s.capabilities_needed)}
        for s in hits
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


def _http_get_json(url: str, timeout: int = 15) -> dict:
    """GET a URL and parse JSON. Browser UA (some hosts 403 bare urllib).
    Raises on network/parse error — callers translate to a graceful note."""
    import json as _json
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ABA discovery)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read())


# Indirection so tests can stub the network without monkeypatching urllib.
_HTTP_GET_JSON = _http_get_json


def search_nf_core(input_: dict) -> dict:
    """Discover nf-core pipelines by intent (item 3). Fetches the public
    nf-co.re pipelines index and ranks it with our BM25 over name +
    description + topics. A discovered pipeline can be catalogued via
    propose_capability(archetype='pipeline'); actually running it needs a
    Nextflow runtime (not yet wired), so adoption is record-only for now."""
    q = (input_.get("query") or "").strip()
    if not q:
        return {"status": "error", "note": "search_nf_core needs a non-empty `query`."}
    limit = int(input_.get("limit") or 8)
    try:
        data = _HTTP_GET_JSON("https://nf-co.re/pipelines.json")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "note": f"Could not reach nf-core registry: {e}"}
    pipelines = data.get("remote_workflows") or data.get("pipelines") or []
    from core.search import BM25
    by_name: dict[str, dict] = {}
    docs = []
    for p in pipelines:
        name = p.get("name") or ""
        if not name:
            continue
        topics = " ".join(p.get("topics") or [])
        by_name[name] = p
        docs.append((name, f"{name} {p.get('description','')} {topics}"))
    ranked = [n for n, _ in BM25(docs).search(q, limit=limit)]
    out = []
    for name in ranked:
        p = by_name[name]
        rels = p.get("releases") or []
        latest = rels[0].get("tag_name") if rels and isinstance(rels[0], dict) else None
        out.append({
            "name": name,
            "description": p.get("description"),
            "topics": p.get("topics") or [],
            "url": f"https://nf-co.re/{name}",
            "latest_release": latest,
        })
    return {"pipelines": out, "total_indexed": len(docs),
            "note": "Adopt one with propose_capability(name, archetype='pipeline'). "
                    "Running pipelines needs a Nextflow runtime (deferred)."}


_DEFAULT_MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


def _mcp_registry_url() -> str:
    """Public MCP server registry. Override via ABA_MCP_REGISTRY_URL to point at
    Smithery / an internal registry without code changes (read at call time)."""
    import os
    return os.environ.get("ABA_MCP_REGISTRY_URL", _DEFAULT_MCP_REGISTRY_URL)


def _mcp_command_hint(server: dict) -> Optional[dict]:
    """Best-effort connection spec from a registry entry's packages/remotes,
    in the shape propose_capability(archetype='mcp_server') expects."""
    for pkg in (server.get("packages") or []):
        reg = (pkg.get("registry_name") or pkg.get("registry_type") or "").lower()
        pname = pkg.get("name") or pkg.get("identifier")
        if not pname:
            continue
        if reg in ("npm", "node"):
            return {"command": "npx", "args": ["-y", pname]}
        if reg in ("pypi", "python"):
            return {"command": "uvx", "args": [pname]}
    for rem in (server.get("remotes") or []):
        if rem.get("url"):
            return {"transport": rem.get("transport_type") or "sse", "url": rem["url"]}
    return None


def search_mcp_registry(input_: dict) -> dict:
    """Discover external MCP servers by intent (item 3). Fetches a public MCP
    registry (configurable via ABA_MCP_REGISTRY_URL) and ranks entries with
    our BM25 over name + description. A hit can be adopted as a capability via
    propose_capability(archetype='mcp_server', connection=...), then
    ensure_capability connects it live so its tools become callable."""
    q = (input_.get("query") or "").strip()
    if not q:
        return {"status": "error", "note": "search_mcp_registry needs a non-empty `query`."}
    limit = int(input_.get("limit") or 8)
    registry_url = _mcp_registry_url()
    try:
        data = _HTTP_GET_JSON(registry_url)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "note": f"Could not reach MCP registry: {e}"}
    servers = data.get("servers") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    from core.search import BM25
    by_id: dict[str, dict] = {}
    docs = []
    for i, s in enumerate(servers or []):
        name = s.get("name") or s.get("id") or f"server-{i}"
        sid = f"{i}:{name}"
        by_id[sid] = s
        docs.append((sid, f"{name} {s.get('description','')}"))
    ranked = [sid for sid, _ in BM25(docs).search(q, limit=limit)]
    out = []
    for sid in ranked:
        s = by_id[sid]
        conn = _mcp_command_hint(s)
        out.append({
            "name": s.get("name") or s.get("id"),
            "description": s.get("description"),
            "repository": (s.get("repository") or {}).get("url") if isinstance(s.get("repository"), dict) else s.get("repository"),
            "connection": conn,
            "adoptable": conn is not None,
        })
    return {"servers": out, "total_indexed": len(docs), "registry": registry_url,
            "note": "Adopt one with propose_capability(name, archetype='mcp_server', "
                    "connection={command,args} or {transport,url}); then ensure_capability "
                    "connects it and its tools become callable as 'server:tool'."}


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
    # Reference catalogue entry (e.g. extracted from biomni): know-how, not a
    # runnable artifact in ABA. Don't pretend to install it.
    if cap.get("reference"):
        return {"status": "reference", "name": cap.get("name"),
                "origin": cap.get("origin"), "source_ref": cap.get("source_ref"),
                "note": f"'{cap.get('name')}' is a reference entry extracted from "
                        f"{cap.get('origin')} — it describes an approach, it isn't "
                        f"runnable here. Implement it with ABA capabilities (search the "
                        f"catalogue / propose_capability for the real libraries), using "
                        f"read_capability for its inputs."}
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
    if prov.get("mcp_server"):
        # Live adoption: connect the external server now so its tools become
        # callable as 'server:tool' for the rest of this session.
        conn = prov["mcp_server"]
        if conn.get("url"):
            return {"status": "deferred", "name": cap.get("name"), "archetype": "mcp_server",
                    "note": "Remote (HTTP/SSE) MCP transport isn't wired yet; only stdio "
                            "(command/args) servers can be connected on demand."}
        from core.runtime.mcp import add_server, ServerConfig
        cfg = ServerConfig(
            name=cap.get("name"),
            command=conn.get("command"),
            args=tuple(conn.get("args") or ()),
            env={str(k): str(v) for k, v in (conn.get("env") or {}).items()},
            cwd=conn.get("cwd"),
        )
        res = add_server(cfg)
        if res.get("status") in ("connected", "already_connected"):
            tools = res.get("tools") or []
            return {"status": "ready", "name": cap.get("name"), "archetype": "mcp_server",
                    "tools": tools,
                    "note": f"Connected; {len(tools)} tool(s) now callable: "
                            f"{', '.join(tools[:8])}{'…' if len(tools) > 8 else ''}."}
        return {"status": "error", "name": cap.get("name"), "archetype": "mcp_server",
                "note": f"Could not connect MCP server: {res.get('note')}"}
    if prov.get("pipeline"):
        pl = prov["pipeline"]
        engine = (pl.get("engine") or "nextflow").lower()
        if engine != "nextflow":
            return {"status": "deferred", "name": cap.get("name"), "archetype": "pipeline",
                    "note": f"Pipeline engine '{engine}' isn't wired yet (only nextflow)."}
        from core.exec import MaterializingExecutor, Provisioning
        try:
            MaterializingExecutor().materialize(Provisioning(conda={"channel": "bioconda", "spec": "nextflow"}))
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": cap.get("name"), "archetype": "pipeline",
                    "note": f"Could not install nextflow: {e}"}
        ref = pl.get("nf_core") or cap.get("name")
        return {"status": "ready", "name": cap.get("name"), "archetype": "pipeline",
                "note": f"nextflow installed and on PATH. Run this pipeline with "
                        f"run_nextflow(pipeline='{ref}', profile='test', ...). "
                        f"(Large runs will route to HPC/remote later — local only for now.)"}
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
    if archetype == "mcp_server":
        # An external MCP server discovered via search_mcp_registry. Provisioning
        # carries the connection spec; ensure_capability connects it live.
        conn = input_.get("connection") or {}
        if not isinstance(conn, dict) or not (conn.get("command") or conn.get("url")):
            return {"status": "error", "name": name,
                    "note": "mcp_server needs connection={command,args[,env]} (stdio) "
                            "or {transport,url} (remote)."}
        spec = {
            "name": name, "version": version, "archetype": "mcp_server",
            "summary": input_.get("summary") or f"{name} (MCP server, adopted on demand)",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"mcp_server": conn},
            "source": input_.get("source") or "mcp_registry",
        }
    elif archetype == "pipeline":
        # An nf-core (or similar) pipeline discovered via search_nf_core. Record
        # only for now — running needs a Nextflow runtime (deferred).
        spec = {
            "name": name, "version": version, "archetype": "pipeline",
            "summary": input_.get("summary") or f"{name} (nf-core pipeline, catalogued)",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"pipeline": {
                "engine": "nextflow",
                "nf_core": name,
                "url": input_.get("url") or f"https://nf-co.re/{name}",
                "revision": input_.get("revision") or version,
            }},
            "source": input_.get("source") or "nf-core",
        }
    elif archetype == "cli":
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
    elif archetype == "mcp_server":
        note = ("Added to the catalog (auto-approved). Call ensure_capability to "
                "connect it live; its tools then appear as 'server:tool' and are callable this session.")
    elif archetype == "pipeline":
        note = ("Catalogued (auto-approved). Running it needs a Nextflow runtime "
                "(not yet wired) — ensure_capability will report it as deferred.")
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
    # Some hosts (e.g. Bioconductor) 403 the default urllib user-agent.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ABA)"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def _nextflow_command(pipeline: str, *, revision=None, profile=None, outdir: str,
                      params: dict | None = None, extra_args=None) -> list[str]:
    """Build the `nextflow run …` argv. Pure function — unit-tested separately."""
    cmd = ["nextflow", "run", pipeline]
    if revision:
        cmd += ["-r", str(revision)]
    if profile:
        cmd += ["-profile", str(profile)]
    cmd += ["-ansi-log", "false", "--outdir", str(outdir)]
    for k, v in (params or {}).items():
        cmd += [f"--{k}", str(v)]
    cmd += list(extra_args or [])
    return cmd


def run_nextflow(input_: dict, ctx: dict | None = None) -> dict:
    """Run a Nextflow / nf-core pipeline. Installs nextflow on demand (conda),
    runs `nextflow run <pipeline>` in the project workspace, returns logs +
    output files. Local execution today; the ExecutionRouter seam is where
    HPC/remote submission plugs in later (kernels.md / capdat_impl.md)."""
    pipeline = (input_.get("pipeline") or "").strip()
    if not pipeline:
        return {"status": "error",
                "note": "run_nextflow needs `pipeline` (e.g. 'nf-core/rnaseq' or 'nextflow-io/hello')."}

    # Remote/HPC seam: many pipelines will eventually run off-box. That routing
    # decision lives here; for now only local synchronous execution is wired.
    if input_.get("remote") or input_.get("background"):
        return {"status": "unsupported_location",
                "note": "Remote/HPC nextflow execution isn't wired yet — only local. "
                        "Re-run without remote/background (long pipelines will move to HPC later)."}

    revision = input_.get("revision")
    profile = input_.get("profile")
    params = input_.get("params") or {}
    timeout_s = max(30, min(int(input_.get("timeout_s") or 1800), 3600))
    cancel_token = (ctx or {}).get("cancel_token")
    from core import projects
    from core.data.workspace import scratch_dir
    project_id = projects.current() or "default"
    run_id = (ctx or {}).get("run_id") or uuid.uuid4().hex
    scratch = scratch_dir(str(project_id), f"nf-{run_id}")
    outdir = input_.get("outdir") or str(Path(scratch) / "results")

    from core.exec import MaterializingExecutor, Provisioning
    ex = MaterializingExecutor()
    try:
        env = ex.materialize(Provisioning(conda={"channel": "bioconda", "spec": "nextflow"}))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "note": f"Could not install nextflow: {e}"}

    cmd = _nextflow_command(pipeline, revision=revision, profile=profile,
                            outdir=outdir, params=params)
    res = ex.exec(env, cmd, cwd=str(scratch), cancel_token=cancel_token, timeout_s=timeout_s)
    if res.timed_out:
        return {"status": "error",
                "note": f"nextflow run timed out ({timeout_s}s). Long pipelines should run "
                        f"on HPC/remote (not yet wired)."}
    if getattr(res, "cancelled", False):
        return {"status": "cancelled", "note": "nextflow run cancelled by the user."}

    from core.exec.run import harvest_artifacts
    plots, tables, out_files = [], [], []
    op = Path(outdir)
    if op.exists():
        plots, tables = harvest_artifacts(op)
        out_files = sorted(str(p.relative_to(op)) for p in op.rglob("*") if p.is_file())[:100]
    return {
        "status": "ok" if res.returncode == 0 else "error",
        "command": " ".join(cmd),
        "returncode": res.returncode,
        "stdout": (res.stdout or "")[:4000],
        "stderr": (res.stderr or "")[:3000],
        "outdir": outdir,
        "outputs": out_files,
        "plots": plots,
        "tables": tables,
        "execution_mode": "stateless",
    }


def restart_kernel_tool(input_: dict, ctx: dict | None = None) -> dict:
    """Clear the current thread's persistent Python session (kernels.md §6)."""
    from core.config import KERNEL_ENABLED
    if not KERNEL_ENABLED:
        return {"status": "noop", "note": "Persistent sessions are disabled; run_python is already stateless."}
    from core.exec.kernels import get_pool
    thread_id = (ctx or {}).get("thread_id") or "default"
    pool = get_pool()
    cleared = [lang for lang in ("python", "r") if pool.restart(str(thread_id), lang)]
    return {"status": "restarted" if cleared else "no_active_session",
            "cleared": cleared,
            "note": "Session(s) cleared; variables reset. The next run_python/run_r starts fresh."}


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
    "run_r": run_r,
    "inspect_upload": inspect_upload,
    "get_provenance": get_provenance,
    "get_dependents": get_dependents,
    "create_scenario": create_scenario,
    "present_plan": present_plan,
    "ask_clarification": ask_clarification,
    "read_skill": read_skill,
    "search_skills": search_skills_tool,
    "read_memory": read_memory_tool,
    "write_memory": write_memory_tool,
    "list_capabilities": list_capabilities_tool,
    "read_capability": read_capability,
    "ensure_capability": ensure_capability,
    "search_pypi": search_pypi,
    "search_bioconda": search_bioconda,
    "search_nf_core": search_nf_core,
    "search_mcp_registry": search_mcp_registry,
    "propose_capability": propose_capability_tool,
    "fetch_url": fetch_url,
    "lookup_sra_runinfo": lookup_sra_runinfo,
    "fetch_ensembl": fetch_ensembl,
    "register_reference": register_reference_tool,
    "find_reference": find_reference_tool,
    "restart_kernel": restart_kernel_tool,
    "run_nextflow": run_nextflow,
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
