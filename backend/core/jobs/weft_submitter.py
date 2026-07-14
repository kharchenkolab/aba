"""WeftSubmitter — background jobs as weft tasks (weft rewrite W2, §4a).

Replaces the in-process LocalSubmitter lane: a background run_python/run_r/
nextflow job becomes a **bare weft task** (`env=None` — the task command names
the served base interpreter explicitly until the W3 base cutover; a NAMED env
job still resolves its interpreter aba-side inside the entry). The task runs
`python -m core.jobs.slurm_entry job_spec.json` — the same node entry the Slurm
lane uses — so artifacts harvest identically and `result.json` stays the
authoritative result. weft contributes what the old lane never had: durable
job state across backend restarts, structured logs/max_rss, and the
**placement provenance block** (§4d) that poll() attaches to the result for
the exec record.

Completion is polled (runner._weft_poll_loop → poll() → _finalize_job →
continuation), mirroring the Slurm sentinel loop; weft's event stream can
replace polling when the W3 sites land.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from core.data.workspace import scratch_dir

_TERMINAL = {"DONE", "FAILED", "CANCELLED"}


def _adapter():
    from core.compute import get_compute
    return get_compute()


def weft_available() -> bool:
    from core.compute import status
    return bool(status().get("ok"))


class WeftSubmitter:
    name = "weft"

    def _run_dir(self, job: dict) -> Path:
        pid = (job.get("params") or {}).get("project_id") or "default"
        return scratch_dir(str(pid), job["id"])

    # ── submit ────────────────────────────────────────────────────────────
    def submit(self, job: dict) -> None:
        params = job.get("params") or {}
        pid = params.get("project_id") or "default"
        kind = job.get("kind") or "run_python"
        run_dir = self._run_dir(job)
        spec_path = run_dir / "job_spec.json"
        result_path = run_dir / "result.json"
        spec_path.write_text(json.dumps({
            "code": params.get("code", ""), "kind": kind, "project_id": str(pid),
            # Run UNDER the Run captured at submit — artifacts land in the Run's
            # work dir and attach to it (same rule as the Slurm lane).
            "run_id": params.get("run_id") or job["id"],
            "timeout_s": int(params.get("timeout_s") or 600),
            "result_path": str(result_path), "env": params.get("env"),
            "gpu": bool((params.get("estimate") or {}).get("gpu")),
            "modules": [],
            "pipeline": params.get("pipeline"), "revision": params.get("revision"),
            "profile": params.get("profile"), "nf_params": params.get("nf_params"),
            "outdir": params.get("outdir"), "execution": params.get("execution"),
            "local_resources": params.get("local_resources"),
        }))
        est = params.get("estimate") or {}
        resources = {"cpus": int(est.get("cores") or 1)}
        if est.get("mem_gb"):
            resources["mem_gb"] = int(est["mem_gb"])
        task = {
            # Bare task: the command names the served base interpreter (the
            # local site inherits the process env, so ABA_* config flows to the
            # entry); PYTHONPATH makes `-m core.jobs.slurm_entry` importable.
            "command": f"{sys.executable} -u -m core.jobs.slurm_entry {spec_path}",
            "site": "local",
            "env_vars": {"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
            "resources": resources,
            "label": (job.get("title") or job["id"])[:200],
        }
        r = _adapter().sync_call("task_submit", task)
        from core.graph.jobs import update_job
        update_job(job["id"], params={**params, "weft_id": r["job_id"],
                                      "submitter": "weft"},
                   project_id=str(pid))

    # ── cancel ───────────────────────────────────────────────────────────
    def cancel(self, job: dict) -> None:
        wid = (job.get("params") or {}).get("weft_id")
        if wid:
            try:
                _adapter().sync_call("task_cancel", wid, why="user cancel from aba")
            except Exception:  # noqa: BLE001 — row is marked cancelled by caller
                pass

    # ── poll ─────────────────────────────────────────────────────────────
    def poll(self, job: dict) -> Optional[dict]:
        """Result-shaped dict once the weft task terminated, else None.
        result.json (written by the entry on the execution side) is
        authoritative; the weft manifest contributes logs for infra deaths and
        the placement/compute provenance block either way."""
        params = job.get("params") or {}
        wid = params.get("weft_id")
        if not wid:
            return None
        try:
            rows = _adapter().sync_call("task_status", wid)
        except Exception:  # noqa: BLE001 — substrate hiccup ≠ job failure
            return None
        state = rows[0]["state"] if rows else None
        if state not in _TERMINAL:
            return None
        result_path = self._run_dir(job) / "result.json"
        if result_path.exists():
            try:
                res = json.loads(result_path.read_text())
            except Exception:  # noqa: BLE001
                res = {"error": f"result.json unreadable for weft job {wid}"}
        elif state == "CANCELLED":
            res = {"status": "cancelled", "note": "cancelled on the compute substrate"}
        else:
            res = {"error": f"weft task {state} with no result.json "
                            f"(infra failure before the entry ran?)"}
        res.setdefault("compute", self._compute_block(wid, state))
        return res

    def _compute_block(self, wid: str, state: str) -> dict:
        """The exec record's weft-sourced compute facts (§4d): task identity +
        placement (circumstance, never identity) + env grade when present."""
        block = {"substrate": "weft", "job_id": wid, "state": state}
        try:
            man = _adapter().sync_call("task_result", wid)
            block["node"] = man.get("node")
            block["env_id"] = man.get("env_id")
            block["wall_s"] = man.get("wall_s")
            block["max_rss_gb"] = man.get("max_rss_gb")
            if state != "DONE" and (man.get("logs") or {}).get("tail"):
                block["log_tail"] = man["logs"]["tail"][-800:]
        except Exception:  # noqa: BLE001
            pass
        try:
            prov = _adapter().sync_call("provenance", wid)
            if isinstance(prov, dict) and prov.get("placement"):
                block["placement"] = prov["placement"]
        except Exception:  # noqa: BLE001
            pass
        return block

    # ── info ─────────────────────────────────────────────────────────────
    def info(self, job: dict) -> dict:
        wid = (job.get("params") or {}).get("weft_id")
        if not wid:
            return {"scheduler": "weft", "state": "unsubmitted"}
        try:
            rows = _adapter().sync_call("task_status", wid)
            row = rows[0] if rows else {}
            return {"scheduler": "weft", "id": wid,
                    "state": row.get("state"), "since": row.get("since")}
        except Exception as e:  # noqa: BLE001
            return {"scheduler": "weft", "id": wid, "error": str(e)}
