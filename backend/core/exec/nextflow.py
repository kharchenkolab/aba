"""Nextflow / nf-core execution core (P0 — HPC-routed pipelines).

`run_nextflow_code` is the reusable execution+harvest function for a Nextflow
pipeline — the workflow analogue of `run_python_code` / `run_r_code`. Both the
in-process worker (runner._run_one) and the Slurm compute-node entry
(jobs.slurm_entry) call it, so a pipeline harvests + provenances identically
either way.

Model (refs.md / misc/nfcore.md): we run the lightweight **Nextflow head
process**; Nextflow's own executor (the site profile, e.g. nf-core/configs
`cbe`) fans each task out as its own scheduler job. So on a cluster the head is
itself a long-lived Slurm job that submits task jobs — we don't wrap the whole
pipeline in one allocation.

Site wiring (the module to load, the profiles to append, the singularity cache,
the work-dir root, head-job resources) is config, not code — see
`nextflow_config()`. Defaults are conservative so this is a no-op off-cluster;
a deployment sets the `nextflow:` block in hpc.yaml (or the ABA_NEXTFLOW_* env).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional


# ── site config ──────────────────────────────────────────────────────────────
_DEFAULT_HEAD = {"cores": 2, "mem_gb": 8, "qos": None, "partition": None, "walltime_h": 24}


def nextflow_config() -> dict:
    """Resolve the site's Nextflow wiring, broadest-default first:

      hpc.yaml `nextflow:` block  →  ABA_NEXTFLOW_* env overrides  →  these defaults.

    Keys:
      module                 Lmod module the Slurm head job `module load`s so
                             `nextflow` is on PATH (e.g. "nextflow/24.10.6"). None
                             → assume nextflow is already on PATH or conda-install it.
      profiles               profiles APPENDED to the caller's -profile (e.g. ["cbe"]).
      singularity_cachedir   NXF_SINGULARITY_CACHEDIR (shared image cache).
      workdir_root           base for Nextflow's -work-dir; a per-run subdir is
                             appended. None → use the run's own scratch dir.
      head                   the head-job Slurm resources {cores, mem_gb, qos,
                             partition, walltime_h} — modest CPU/mem, LONG walltime.
    """
    cfg: dict = {}
    try:
        from core.jobs.hpc_config import hpc_config
        cfg = dict((hpc_config() or {}).get("nextflow") or {})
    except Exception:  # noqa: BLE001 — config is optional
        cfg = {}

    def _csv(v):
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return [t.strip() for t in str(v or "").split(",") if t.strip()]

    module = os.environ.get("ABA_NEXTFLOW_MODULE", cfg.get("module"))
    profiles = _csv(os.environ.get("ABA_NEXTFLOW_PROFILES")) or _csv(cfg.get("profiles"))
    cachedir = os.environ.get("ABA_NEXTFLOW_CACHEDIR", cfg.get("singularity_cachedir"))
    workdir_root = os.environ.get("ABA_NEXTFLOW_WORKDIR", cfg.get("workdir_root"))
    # A site config (`-c <file>`) — e.g. an ABA-pinned cbe-derived profile that sets
    # the slurm executor/queue/QOS + singularity, WITHOUT nf-core/configs' stale
    # `process.module` loads. Applied to every run when set.
    config_file = os.environ.get("ABA_NEXTFLOW_CONFIG", cfg.get("config_file"))
    head = {**_DEFAULT_HEAD, **(cfg.get("head") or {})}
    return {"module": module or None, "profiles": profiles,
            "singularity_cachedir": cachedir or None,
            "workdir_root": workdir_root or None, "config_file": config_file or None,
            "head": head}


def merged_profile(caller_profile: Optional[str], site_profiles: list[str]) -> Optional[str]:
    """Caller's -profile tokens first (they win in Nextflow), then any site
    profiles not already present. Returns a comma string, or None if empty."""
    out: list[str] = []
    for t in [t.strip() for t in (caller_profile or "").split(",") if t.strip()]:
        if t not in out:
            out.append(t)
    for t in site_profiles:
        if t not in out:
            out.append(t)
    return ",".join(out) or None


# ── pure helpers ─────────────────────────────────────────────────────────────
def nextflow_command(pipeline: str, *, revision=None, profile=None, outdir: str,
                     params: dict | None = None, work_dir: Optional[str] = None,
                     reports_dir: Optional[str] = None, resume: bool = True,
                     config_file: Optional[str] = None, extra_args=None) -> list[str]:
    """Build the `nextflow run …` argv. Pure function — unit-tested."""
    cmd = ["nextflow", "run", pipeline]
    if revision:
        cmd += ["-r", str(revision)]
    if config_file:
        cmd += ["-c", str(config_file)]
    if profile:
        cmd += ["-profile", str(profile)]
    cmd += ["-ansi-log", "false"]
    if work_dir:
        cmd += ["-work-dir", str(work_dir)]
    if reports_dir:
        rd = Path(reports_dir)
        cmd += ["-with-trace", str(rd / "trace.txt"),
                "-with-report", str(rd / "report.html"),
                "-with-timeline", str(rd / "timeline.html")]
    if resume:
        cmd += ["-resume"]
    cmd += ["--outdir", str(outdir)]
    for k, v in (params or {}).items():
        cmd += [f"--{k}", str(v)]
    cmd += list(extra_args or [])
    return cmd


def parse_trace_containers(trace_path) -> list[str]:
    """Unique container images from a `-with-trace` TSV (`container` column) — the
    per-process engine env for workflow provenance. [] on any miss."""
    try:
        lines = Path(trace_path).read_text().splitlines()
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


# ── the reusable execution+harvest function ──────────────────────────────────
def run_nextflow_code(pipeline: str, *, project_id: str, run_id: Optional[str] = None,
                      revision: Optional[str] = None, profile: Optional[str] = None,
                      params: Optional[dict] = None, outdir: Optional[str] = None,
                      work_dir: Optional[str] = None, timeout_s: int = 3600,
                      cancel_token=None, stream: bool = False) -> dict:
    """Run a Nextflow pipeline head process in the project workspace, harvest its
    --outdir, and return the standard background result_obj (returncode/stdout/
    stderr/plots/tables/files/cwd) plus a `workflow` provenance block. Resolves
    `nextflow` from PATH (a Slurm head job `module load`s it; see slurm_submitter)
    and otherwise conda-installs it for the off-cluster local path."""
    import uuid as _uuid
    from core.data.workspace import scratch_dir
    from core.exec import MaterializingExecutor, Provisioning
    from core.exec.run import harvest_artifacts
    from core.exec.output_cap import snip_middle

    run_id = run_id or _uuid.uuid4().hex
    scratch = scratch_dir(str(project_id), str(run_id))
    cfg = nextflow_config()
    prof = merged_profile(profile, cfg["profiles"])
    outdir = outdir or str(Path(scratch) / "results")
    # Nextflow's work-dir (large, churny intermediates) → fast scratch if the site
    # gave a root; else under the run scratch. A per-run subdir keeps runs isolated.
    if work_dir is None:
        if cfg["workdir_root"]:
            work_dir = str(Path(os.path.expandvars(cfg["workdir_root"])) / str(run_id))
        else:
            work_dir = str(Path(scratch) / "work")
    reports = Path(scratch) / "nf_reports"
    reports.mkdir(parents=True, exist_ok=True)
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    ex = MaterializingExecutor()
    # nextflow on PATH (module-loaded on the Slurm head, or already installed) →
    # use the base env and inherit it; else conda-install it (off-cluster local).
    try:
        if shutil.which("nextflow"):
            menv = ex.materialize(Provisioning(), cancel_token=cancel_token)
        else:
            menv = ex.materialize(Provisioning(conda={"channel": "bioconda", "spec": "nextflow"}),
                                  cancel_token=cancel_token)
    except Exception as e:  # noqa: BLE001
        return {"error": f"Could not provision nextflow: {e}"}

    env_vars = {"NXF_HOME": str(Path(scratch) / ".nextflow")}
    if cfg["singularity_cachedir"]:
        env_vars["NXF_SINGULARITY_CACHEDIR"] = cfg["singularity_cachedir"]
        env_vars["SINGULARITY_CACHEDIR"] = cfg["singularity_cachedir"]

    cmd = nextflow_command(pipeline, revision=revision, profile=prof, outdir=outdir,
                           params=params, work_dir=work_dir, reports_dir=str(reports),
                           config_file=cfg.get("config_file"))
    res = ex.exec(menv, cmd, cwd=str(scratch), cancel_token=cancel_token,
                  timeout_s=timeout_s, env_vars=env_vars, stream=stream)

    if getattr(res, "timed_out", False):
        return {"error": f"nextflow run timed out ({timeout_s}s limit)"}
    if getattr(res, "cancelled", False):
        return {"status": "cancelled",
                "note": "Nextflow run was cancelled by the user. No further work happened."}

    plots, tables, files, out_files = [], [], [], []
    op = Path(outdir)
    if op.exists():
        try:
            plots, tables, files, _warns = harvest_artifacts(op, project_id=str(project_id))
        except Exception:  # noqa: BLE001 — harvest is best-effort
            plots, tables, files = [], [], []
        out_files = sorted(str(p.relative_to(op)) for p in op.rglob("*") if p.is_file())[:100]

    nf_ver = ""
    try:
        vr = ex.exec(menv, ["nextflow", "-version"], cwd=str(scratch), timeout_s=30)
        m = re.search(r"version\s+([0-9][0-9.]*)", (getattr(vr, "stdout", "") or "")
                      + (getattr(vr, "stderr", "") or ""))
        nf_ver = m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        nf_ver = ""

    cmd_str = " ".join(cmd)
    return {
        "returncode": res.returncode,
        "stdout": snip_middle(res.stdout or ""),
        "stderr": snip_middle(res.stderr or ""),
        "plots": plots, "tables": tables, "files": files,
        "cwd": str(scratch),
        "outdir": outdir, "outputs": out_files,
        "execution_mode": "workflow",
        # consumed by runner._write_exec_record_for_job (kind:workflow branch)
        "workflow": {
            "engine": {"name": "nextflow", "version": nf_ver},
            "per_process_images": parse_trace_containers(reports / "trace.txt"),
            "pipeline": pipeline, "revision": revision, "profile": prof,
            "params": params or {}, "outputs": out_files[:50], "command": cmd_str,
        },
        "command": cmd_str,
    }
