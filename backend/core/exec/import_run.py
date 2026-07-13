"""Import an EXTERNAL results directory as a Run (misc/external_import.md).

`import_run_code` is the executor for a `kind="import_run"` background job. It does NOT compute
anything — it SCRAPES a directory a collaborator produced (an nf-core / custom pipeline the user
did not launch through ABA) and returns the SAME result_obj shape as `run_nextflow_code`, so the
whole finalize → register-artifacts → refresh-manifest → settle → continuation "present" chain
runs unchanged (the agent presents the imported Run exactly like a completed pipeline).

Storage contract (the only real difference from a native run):
  * The external dir (Location 1) is READ-ONLY and never written to. The Run's `artifact_path`
    points at it, so the manifest browses the WHOLE tree with zero copy (served read-only via
    /api/runs/<id>/file).
  * Only a high-signal CAP of small viewables is copied into the artifact store as pinnable child
    entities (harvest_artifacts max_files); the bulk stays referenced.
  * nf-core QC is surfaced by reusing parse_multiqc + publish_multiqc_report (the report is copied
    to a servable /artifacts URL so the agent can hand the user a clickable report).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from core import config

# An outside results tree can hold hundreds of per-sample QC files. They stay fully browsable via
# the Run manifest (no copy); we only entity-fy this many as pinnable high-signal artifacts.
IMPORT_HARVEST_CAP = config.settings.import_harvest_cap.get()


def _detect_run_info(src: Path) -> dict:
    """Best-effort provenance for repeatability: what can we tell about how this was produced,
    just by looking? nf-core writes a `pipeline_info/` dir (execution reports, the resolved
    params, the nextflow command). We only flag its presence + capture a params file path here;
    the agent can read those via the browsable manifest to reconstruct a re-run. Practical, not
    exhaustive — an external run often ISN'T exactly repeatable, and that's fine."""
    info: dict = {"engine": "external"}
    pi = src / "pipeline_info"
    if pi.is_dir():
        info["engine"] = "nextflow"
        info["has_pipeline_info"] = True
        try:
            params = sorted(str(p.relative_to(src)) for p in pi.glob("params_*.json"))
            if params:
                info["params_file"] = params[-1]
            # nf-core writes software/pipeline versions here too — a strong repeat hint.
            if list(pi.glob("nf_core_*_software_*")) or (pi / "software_versions.yml").exists():
                info["has_versions"] = True
        except OSError:
            pass
    return info


def import_run_code(source_dir: str, *, project_id: str, run_id: str,
                    pipeline: Optional[str] = None, revision: Optional[str] = None,
                    timeout_s: int = 1800, cancel_token=None, stream: bool = False) -> dict:
    """Scrape `source_dir` into the standard background result_obj. returncode 0 on success; a
    non-zero returncode + `error` when the dir is gone (→ _finalize_job's failed path → the agent
    is told the import failed). Never writes to `source_dir`."""
    from core.data.workspace import scratch_dir
    from core.data import external_ref
    from core.exec.run import harvest_artifacts
    from core.exec.output_cap import snip_middle

    src = Path(source_dir)
    scratch = scratch_dir(str(project_id), str(run_id))   # local, writable — for run.log etc.
    if not src.exists():
        return {"returncode": 1,
                "error": f"external results directory not found or unreadable: {source_dir}",
                "stdout": "", "stderr": "", "plots": [], "tables": [], "files": [],
                "cwd": str(scratch)}

    plots, tables, files = [], [], []
    warns: list[str] = []
    try:
        plots, tables, files, warns = harvest_artifacts(
            src, project_id=str(project_id), max_files=IMPORT_HARVEST_CAP)
    except Exception:  # noqa: BLE001 — harvest is best-effort; the manifest still browses the tree
        plots, tables, files = [], [], []

    out_files = sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file())[:100]

    # nf-core QC — reuse the exact machinery a native completion uses.
    multiqc: dict = {}
    try:
        from core.exec.nextflow import parse_multiqc, publish_multiqc_report
        multiqc = parse_multiqc(src) or {}
        try:
            url = publish_multiqc_report(src, str(project_id), str(run_id))
            if url:
                multiqc["report_url"] = url
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 — parse_multiqc is best-effort
        multiqc = {}

    fp = external_ref.fingerprint(str(src))
    detected = _detect_run_info(src)
    n_art = len(plots) + len(tables) + len(files)
    n_files = fp.get("n_files")
    summary = (f"Imported external run from {src} — harvested {n_art} artifact(s); "
               f"{n_files} file(s) referenced in place (read-only).")
    if multiqc.get("n_samples"):
        summary += f" MultiQC: {multiqc['n_samples']} sample(s)."

    return {
        "returncode": 0,
        "stdout": snip_middle(summary),
        "stderr": "\n".join(warns) if warns else "",
        "plots": plots, "tables": tables, "files": files,
        "cwd": str(scratch),
        "outdir": str(src), "outputs": out_files,
        "multiqc": multiqc,
        "execution_mode": "import",
        "import": {"source_dir": str(src), "fingerprint": fp, "detected": detected},
        # Routed to _write_workflow_exec_record_for_job (a pipeline provenance record, not a script).
        "workflow": {
            "engine": {"name": detected.get("engine") or "external"},
            "imported": True, "source_dir": str(src),
            "pipeline": pipeline or None, "revision": revision or None,
            "params": {}, "outputs": out_files[:50], "command": f"(imported from {src})",
            "multiqc": multiqc,
        },
        "command": f"import_run {src}",
    }
