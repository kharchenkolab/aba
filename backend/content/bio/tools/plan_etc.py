"""Plan / scenario / write-memory / runtime-control bio tool impls
(WU-3-tail). Mix of pure (create_scenario, present_plan, ask_clarification
stubs, write_memory_tool) and ctx-using (restart_kernel_tool, run_nextflow
+ its env-checking helpers).

`present_plan` and `ask_clarification` are intercepted by guide.py
BEFORE the dispatcher runs (the bio impls here are stubs returning a
placeholder status); they remain registered on aba_core so the agent's
tool catalog learns about them."""

from __future__ import annotations
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional


# Container / env profile names — used by the nextflow runner's env-check
# helpers (`_available_container_engines`, `_nextflow_env_blocker`).
_CONTAINER_ENGINES = ("docker", "singularity", "apptainer", "podman", "charliecloud", "shifter", "sarus")
_CONDA_PROFILES = ("conda", "mamba", "micromamba")


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


def ask_clarification(input_: dict) -> dict:
    """No-op server-side, like present_plan. The actual halt + SSE emission
    happens in guide.py's tool-dispatch branch; this stub exists so
    EXECUTORS.get('ask_clarification') doesn't fall through to 'Unknown tool'
    if the dispatch order ever changes."""
    return {"status": "asked",
            "note": "Question shown to the user. Stop here and wait for "
                    "their reply before continuing."}


def _available_container_engines() -> list[str]:
    """Container/runtime engines actually on PATH (nf-core needs one to run a
    pipeline's processes). conda-as-backend is handled separately."""
    import shutil
    return [e for e in _CONTAINER_ENGINES if shutil.which(e)]


def _nextflow_env_blocker(pipeline: str, profile: Optional[str]) -> Optional[dict]:
    """F6: fail fast (instead of timing out) when the run can't possibly execute
    here. Two cases: (a) the profile names a container engine that isn't
    installed; (b) it's an nf-core pipeline with no backend profile and no
    container engine on the box. Returns an error dict, or None to proceed."""
    tokens = {t.strip() for t in (profile or "").split(",") if t.strip()}
    avail = _available_container_engines()
    requested = tokens & set(_CONTAINER_ENGINES)
    if requested and not (requested & set(avail)):
        return {"status": "unsupported_environment", "pipeline": pipeline,
                "note": f"profile requests {sorted(requested)} but none are available here "
                        f"(PATH has: {avail or 'no container engine'}). nf-core needs a container "
                        f"engine (docker/singularity/apptainer) or a conda profile — install one, "
                        f"use -profile test,conda, or run on HPC/remote (deferred)."}
    if (not requested and not (tokens & set(_CONDA_PROFILES)) and not avail
            and pipeline.lower().startswith("nf-core/")):
        return {"status": "unsupported_environment", "pipeline": pipeline,
                "note": "No container engine (docker/singularity/apptainer) detected and no "
                        "conda profile requested. nf-core pipelines need a software backend to run "
                        "their processes — add a backend profile (test,docker / test,singularity / "
                        "test,conda) once one is available, or run on HPC/remote (deferred)."}
    return None


def _parse_nextflow_containers(trace_path) -> list[str]:
    """Unique container images from a nextflow `-with-trace` TSV (the `container`
    column) — the per-process env for workflow provenance. [] on any miss."""
    from pathlib import Path as _P
    try:
        lines = _P(trace_path).read_text().splitlines()
        if not lines:
            return []
        header = lines[0].split("\t")
        if "container" not in header:
            return []
        ci = header.index("container")
        seen: list[str] = []
        for ln in lines[1:]:
            parts = ln.split("\t")
            if len(parts) > ci:
                c = parts[ci].strip()
                if c and c not in ("-", "") and c not in seen:
                    seen.append(c)
        return seen
    except Exception:  # noqa: BLE001
        return []


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

    revision = input_.get("revision")
    profile = input_.get("profile")
    # F6: fail fast if the environment can't run this (e.g. profile names docker,
    # which CBE doesn't have) instead of letting nextflow time out. Checks PATH on
    # THIS node; singularity/apptainer are system-wide here so cbe/singularity
    # profiles pass. Applies to both the background and sync paths.
    blocked = _nextflow_env_blocker(pipeline, profile)
    if blocked is not None:
        return blocked

    # P2: pin the revision + validate params against the pipeline's nextflow_schema.json
    # BEFORE anything hits Slurm. Best-effort — a pipeline with no schema (or a fetch
    # miss) just skips; we never block a run on a network hiccup, only on real param errors.
    from core.exec import nextflow_schema as _ns
    if not revision and pipeline.lower().startswith("nf-core/"):
        revision = _ns.latest_release(pipeline) or revision        # reproducible: pin latest release
    _pre_warnings: list = []
    _schema = _ns.fetch_schema(pipeline, revision)
    if _schema:
        _v = _ns.validate_params(_schema, input_.get("params") or {})
        _pre_warnings = _v.get("warnings") or []
        if not _v["ok"]:
            return {"status": "invalid_params", "pipeline": pipeline, "revision": revision,
                    "errors": _v["errors"], "warnings": _pre_warnings,
                    "note": ("Params don't satisfy the pipeline's schema — fix them before "
                             "launching (nothing was submitted). Call describe_pipeline for the "
                             f"full param list. Errors: {'; '.join(_v['errors'])}")}

    # Background / HPC (the path for real pipelines): submit the run as its own
    # job. The Nextflow HEAD runs as a long-lived Slurm job and fans each task out
    # via the site executor (e.g. nf-core/configs `cbe`). The agent gets a deferred
    # handle and is resumed on completion — same contract as run_python(background).
    if input_.get("background") or input_.get("remote"):
        from core.jobs.runner import submit_nextflow_job, BACKGROUND_DEFAULT_TIMEOUT_S
        from content.bio.lifecycle.runs import active_run_id
        from core import projects as _proj
        pid = _proj.current() or "default"
        tid = (ctx or {}).get("thread_id")
        est_min = float(input_.get("estimated_runtime_min") or 0)
        # Estimates are rough → 2× headroom; default 1 h; 24 h backstop.
        bg_timeout = min(int(est_min * 60 * 2), 24 * 3600) if est_min else BACKGROUND_DEFAULT_TIMEOUT_S
        job = submit_nextflow_job(
            pipeline=pipeline, title=input_.get("title") or f"Nextflow: {pipeline}",
            focus_entity_id=(ctx or {}).get("focus_entity_id"),
            revision=revision, profile=profile,
            nf_params=input_.get("params") or {}, outdir=input_.get("outdir"),
            timeout_s=bg_timeout, project_id=str(pid),
            thread_id=str(tid) if tid else None,
            run_id=active_run_id(str(tid)) if tid else None,
            estimate={"runtime_min": est_min})
        note = (f"Submitted Nextflow pipeline '{pipeline}'"
                + (f" @ {revision}" if revision else "")
                + f" as background job {job['id']}. The head runs as a Slurm job and fans "
                f"its tasks out via the site executor; I'll continue when it finishes.")
        if _pre_warnings:
            note += " Param warnings: " + "; ".join(_pre_warnings)
        return {
            "deferred": True, "deferred_id": job["id"], "job_id": job["id"],
            "status": "submitted", "warnings": _pre_warnings, "note": note,
        }
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
        env = ex.materialize(Provisioning(conda={"channel": "bioconda", "spec": "nextflow"}),
                             cancel_token=cancel_token)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "note": f"Could not install nextflow: {e}"}

    from core.runtime import progress
    progress.emit(f"nextflow: launching {pipeline}"
                  + (f" (-profile {profile})" if profile else "") + "…", phase="nextflow")
    _trace = Path(scratch) / "aba_nf_trace.txt"
    cmd = _nextflow_command(pipeline, revision=revision, profile=profile,
                            outdir=outdir, params=params,
                            extra_args=["-with-trace", str(_trace)])  # provenance: per-process containers
    res = ex.exec(env, cmd, cwd=str(scratch), cancel_token=cancel_token, timeout_s=timeout_s)
    if res.timed_out:
        return {"status": "error",
                "note": f"nextflow run timed out ({timeout_s}s). Long pipelines should run "
                        f"on HPC/remote (not yet wired)."}
    if getattr(res, "cancelled", False):
        return {"status": "cancelled", "note": "nextflow run cancelled by the user."}

    from core.exec.run import harvest_artifacts
    plots, tables, files, out_files = [], [], [], []
    op = Path(outdir)
    if op.exists():
        plots, tables, files, _warns = harvest_artifacts(op)
        out_files = sorted(str(p.relative_to(op)) for p in op.rglob("*") if p.is_file())[:100]
    from core.exec.output_cap import snip_middle
    cmd_str = " ".join(cmd)
    # Provenance (provenance.md Phase 3): a kind:workflow exec record — engine +
    # the reproducible command + params + produced. The pipeline+revision pin the
    # workflow (re-running `nextflow run <pipeline> -r <rev>` reproduces it);
    # per-process container digests are a refinement (needs `-with-trace` parsing).
    exec_id = None
    try:
        from datetime import datetime as _dt, timezone as _tz
        from core.graph import exec_records as _er
        from core.exec.fingerprint import code_hash as _ch
        produced = []
        for _grp, _knd in ((plots, "figure"), (tables, "table"), (files, "file")):
            for _i, _a in enumerate(_grp or []):
                if isinstance(_a, dict):
                    produced.append({"kind": _knd, "idx": _i, "url": _a.get("url"),
                                     "name": _a.get("original_name") or _a.get("name")})
                else:
                    produced.append({"kind": _knd, "idx": _i})
        _now = _dt.now(_tz.utc).isoformat()
        nf_ver = ""
        try:
            import re as _re
            _vr = ex.exec(env, ["nextflow", "-version"], cwd=str(scratch), timeout_s=30)
            _m = _re.search(r"version\s+([0-9][0-9.]*)", (getattr(_vr, "stdout", "") or "")
                            + (getattr(_vr, "stderr", "") or ""))
            nf_ver = _m.group(1) if _m else ""
        except Exception:  # noqa: BLE001
            nf_ver = ""
        exec_id = _er.create(
            thread_id=str((ctx or {}).get("thread_id") or "default"),
            run_id=(ctx or {}).get("run_id"),
            tool_use_id=(ctx or {}).get("tool_use_id"),
            tool_name="run_nextflow",
            status="ok" if res.returncode == 0 else "error",
            code=cmd_str, code_hash=_ch(cmd_str),
            started_at=_now, completed_at=_now, cwd=str(scratch),
            payload={
                "kind": "workflow",
                "engine": {"name": "nextflow", "version": nf_ver},
                # env (provenance.md §6 workflow case): the per-process container
                # images Nextflow actually used — the reproducible engine env.
                "env": {"per_process_images": _parse_nextflow_containers(_trace)},
                "params": {"pipeline": pipeline, "revision": revision,
                           "profile": profile, "params": params},
                "produced": produced,
                "outputs": out_files[:50],
                "stdout_tail": snip_middle(res.stdout or ""),
                "stderr_tail": snip_middle(res.stderr or ""),
                "exit_code": res.returncode,
            })
    except Exception as _e:  # noqa: BLE001 — never block the user-visible result
        pass
    return {
        "status": "ok" if res.returncode == 0 else "error",
        "command": cmd_str,
        "returncode": res.returncode,
        "stdout": snip_middle(res.stdout or ""),
        "stderr": snip_middle(res.stderr or ""),
        "outdir": outdir,
        "outputs": out_files,
        "plots": plots,
        "tables": tables,
        "files": files,
        "execution_mode": "stateless",
        "exec_id": exec_id,
    }


def describe_pipeline(input_: dict, ctx: dict | None = None) -> dict:
    """Describe a Nextflow / nf-core pipeline's parameters from its
    nextflow_schema.json — required params, types, defaults, allowed values, help —
    so the agent can build a correct run_nextflow call (and explain the inputs to the
    user) WITHOUT guessing. Best-effort: a pipeline shipping no schema returns a note."""
    pipeline = (input_.get("pipeline") or "").strip()
    if not pipeline:
        return {"status": "error", "note": "describe_pipeline needs `pipeline` (e.g. 'nf-core/rnaseq')."}
    from core.exec import nextflow_schema as _ns
    revision = input_.get("revision")
    latest = _ns.latest_release(pipeline) if pipeline.lower().startswith("nf-core/") else None
    schema = _ns.fetch_schema(pipeline, revision or latest)
    if not schema:
        return {"status": "no_schema", "pipeline": pipeline, "latest_release": latest,
                "docs": _ns.pipeline_doc_links(pipeline, revision or latest),
                "note": ("No nextflow_schema.json found — params can't be validated. Read the "
                         "pipeline's docs (see `docs`) and pass its `--<k> <v>` params to run_nextflow.")}
    groups: dict[str, list] = {}
    for p in _ns.parse_params(schema):
        groups.setdefault(p["group"], []).append({
            "name": p["name"], "type": p["type"], "required": p["required"],
            "default": p["default"], "enum": p["enum"], "help": (p["help"] or "")[:140]})
    required = sorted(p["name"] for grp in groups.values() for p in grp if p["required"])
    # P2.5: the INPUT format — the samplesheet columns the agent must build `--input`
    # from (from assets/schema_input.json). This is what enables reliable data prep.
    input_format = None
    isch = _ns.fetch_input_schema(pipeline, revision or latest)
    if isch:
        cols = _ns.parse_input_columns(isch)
        cols = [{**c, "help": (c.get("help") or "")[:160]} for c in cols]
        input_format = {
            "description": (isch.get("description") or "").strip(),
            "required_columns": [c["name"] for c in cols if c["required"]],
            "columns": cols,
            "note": ("Build the `--input` file (usually a CSV) with EXACTLY these columns, "
                     "one row per sample, from the user's data before launching."),
        }
    return {"status": "ok", "pipeline": pipeline, "revision": revision or latest,
            "latest_release": latest, "required": required, "param_groups": groups,
            "input_format": input_format,
            "docs": _ns.pipeline_doc_links(pipeline, revision or latest),
            "note": (f"{sum(len(v) for v in groups.values())} params in {len(groups)} groups; "
                     f"required: {', '.join(required) or 'none'}. "
                     + (f"Input: a samplesheet with columns {', '.join(c['name'] for c in input_format['columns'])} "
                        f"(required: {', '.join(input_format['required_columns']) or 'none'}). "
                        if input_format else "")
                     + "Pass params to run_nextflow as a dict; --outdir is set automatically. "
                     + "If unsure about the input format or an output, read `docs` (usage/output).")}


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
