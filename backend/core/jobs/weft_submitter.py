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
import re
import sys
from pathlib import Path
from typing import Optional

from core.data.workspace import scratch_dir

_TERMINAL = {"DONE", "FAILED", "CANCELLED"}
# controller-side fetch cap for detached results — matches the harvest
# per-file cap; bigger files stay on the node (kept, (run,rel)-addressable)
_DETACHED_FETCH_BYTES = 50 * 1024 * 1024


def _mismatch_platform(e) -> Optional[str]:
    """The site's platform out of weft's env.platform_mismatch error
    ('... but site X is linux-aarch64') — drives the lazy re-lock."""
    if getattr(e, "code", "") != "env.platform_mismatch":
        return None
    m = re.search(r"is ([a-z0-9_]+-[a-z0-9_]+)\s*$", getattr(e, "detail", "") or "")
    return m.group(1) if m else None


def _row_mismatch_platform(task_err) -> Optional[str]:
    """Same, from a TASK ROW's error payload — this weft surfaces the
    mismatch asynchronously (at realize), as a dict with hints."""
    if isinstance(task_err, dict):
        if task_err.get("error") != "env.platform_mismatch":
            return None
        hint = (task_err.get("hints") or {}).get("site_platform")
        if hint:
            return str(hint)
        m = re.search(r"is ([a-z0-9_]+-[a-z0-9_]+)\s*$",
                      str(task_err.get("detail") or ""))
        return m.group(1) if m else None
    if isinstance(task_err, str) and "env.platform_mismatch" in task_err:
        m = re.search(r"'site_platform':\s*'([a-z0-9_]+-[a-z0-9_]+)'", task_err) \
            or re.search(r"is ([a-z0-9_]+-[a-z0-9_]+)", task_err)
        return m.group(1) if m else None
    return None


def _adapter():
    from core.compute import get_compute
    return get_compute()


def weft_available() -> bool:
    from core.compute import status
    return bool(status().get("ok"))


def _aba_env_vars() -> dict:
    """The ABA_* config a node-side entry needs (the same contract the sbatch
    lane's --export=ALL provided): every set ABA_* var, so the entry's registry
    reads resolve identically on the node (shared-FS deployment)."""
    import os
    return {k: v for k, v in os.environ.items() if k.startswith("ABA_")}


def weft_slurm_site() -> Optional[str]:
    """The deployment's slurm-kind weft site (declared in weft-sites.yaml), or
    None. This is what makes ABA_BATCH_SUBMITTER=slurm route through weft —
    data-driven: a deployment that declared no cluster site keeps the legacy
    sbatch lane."""
    if not weft_available():
        return None
    try:
        for s in _adapter().sync_call("sites_list"):
            if s.get("kind") == "slurm":
                return s.get("name")
    except Exception:  # noqa: BLE001
        return None
    return None


def site_contract(site: str) -> str:
    """'shared-fs' | 'detached' for a declared site — data-driven. The aba
    sidecar's explicit `contract` wins; else a site with a `host` (remote
    transport) is DETACHED from this controller (no shared FS, possibly a
    different OS), while a host-less site (local transport on the submit
    node — the deployment-declared cluster case) is shared-fs."""
    if site == "local":
        return "shared-fs"
    try:
        from core.compute.sites_config import aba_keys, list_declared_sites
        c = (aba_keys(site) or {}).get("contract")
        if c in ("shared-fs", "detached"):
            return c
        for e in list_declared_sites():
            if e.get("name") == site:
                return "detached" if (e.get("config") or {}).get("host") else "shared-fs"
    except Exception:  # noqa: BLE001
        pass
    # Not in the deployment yaml (registered ad hoc): DETACHED. Shared-fs is a
    # DEPLOYMENT property (installer-declared, host-less transport on the
    # submit node) — it must be declared, never guessed: the detached
    # transport works everywhere, while assuming shared-fs on a foreign
    # machine fails with a meaningless exit 127.
    return "detached"


def declared_compute_sites() -> list[dict]:
    """[{name, kind, contract}] for every declared weft site — what
    describe_compute shows the agent and what site= validation checks."""
    out = []
    try:
        for s in _adapter().sync_call("sites_list"):
            name = s.get("name")
            if not name:
                continue
            out.append({"name": name, "kind": s.get("kind"),
                        "contract": site_contract(name)})
    except Exception:  # noqa: BLE001
        pass
    return out


def _walltime(timeout_s: int) -> str:
    """Explicit walltime from the job's timeout ceiling (doctrine: size
    walltime explicitly — an unspecified ask hits site defaults, an over-cap
    ask is REFUSED upfront by weft instead of pending forever)."""
    t = max(60, int(timeout_s))
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"


class WeftSubmitter:
    """site='local' → this node (the W2 lane); site=<slurm-kind weft site> →
    the cluster (W3.3), same entry + result.json contract over the shared FS
    the deployment already guarantees (server + nodes see the same paths)."""

    name = "weft"

    def __init__(self, site: str = "local"):
        self.site = site

    def _run_dir(self, job: dict) -> Path:
        pid = (job.get("params") or {}).get("project_id") or "default"
        return scratch_dir(str(pid), job["id"])

    # ── submit ────────────────────────────────────────────────────────────
    def submit(self, job: dict) -> None:
        # DETACHED sites (no shared FS with this controller — a personal remote
        # machine, a foreign cluster): the code travels AS DATA (a CAS-staged
        # payload) and runs under the NODE's python — never the controller's
        # entry, which doesn't exist there. Shared-fs sites keep the fast path.
        if self.site != "local" and site_contract(self.site) == "detached":
            self._submit_detached(job)
            return
        params = job.get("params") or {}
        pid = params.get("project_id") or "default"
        kind = job.get("kind") or "run_python"
        run_dir = self._run_dir(job)
        spec_path = run_dir / "job_spec.json"
        result_path = run_dir / "result.json"
        timeout_s = int(params.get("timeout_s") or 600)
        # W3.4/W3.5 (pack deployments): the job runs INSIDE its weft env — the task
        # carries `env=<EnvID>` and weft activates it on the node (a DEFAULT-env job
        # rides the project session's SNAPSHOT — a frozen, dirty-cached EnvID, so the
        # job is reproducible and its exec record carries true env identity; an
        # isolated-env job rides that named env's EnvID). aba resolves only the
        # IDENTITY here (a store op — NO realize, so this is strategy-blind: a squashfs
        # env has no raw prefix at rest, but weft mounts it for the task and sets
        # CONDA_PREFIX; the node entry reads that — see slurm_entry). `interp` is NO
        # LONGER resolved aba-side (the old raw `<prefix>/bin/python` broke under the
        # squashfs realization strategy that parallel-FS/cluster roots get).
        env_id = None
        if kind in ("run_python", "run_r"):
            from core.compute import base_env, named_envs, project_env
            lang = "r" if kind == "run_r" else "python"
            try:
                if params.get("env"):
                    row = named_envs.resolve(str(pid), params["env"])
                    if row is None:
                        print(f"[jobs.weft] unknown isolated env {params['env']!r} for "
                              f"project {pid} — the job will fail loudly on the node")
                    else:
                        env_id = row["env_id"]
                else:
                    base_env.require(lang)      # weft-only: no served-base fallback
                    env_id = project_env.snapshot(str(pid), lang)   # identity; store op, no realize
            except Exception as e:  # noqa: BLE001
                print(f"[jobs.weft] env identity resolution failed ({e}) — the job "
                      f"will fail loudly on the node (no served-base fallback)")
        spec_path.write_text(json.dumps({
            "code": params.get("code", ""), "kind": kind, "project_id": str(pid),
            # Run UNDER the Run captured at submit — artifacts land in the Run's
            # work dir and attach to it (same rule as the legacy Slurm lane).
            "run_id": params.get("run_id") or job["id"],
            "timeout_s": timeout_s,
            # env=None on the SPEC: the node entry must NOT re-resolve an env (no
            # substrate there); it runs the activated task env via CONDA_PREFIX.
            "result_path": str(result_path), "env": None,
            "env_id": env_id, "interp": None,
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
        if est.get("gpu"):
            resources["gpus"] = 1
        if self.site != "local":
            # Scheduler sites get an explicit walltime (timeout ceiling + grace
            # for staging); weft refuses an over-cap ask upfront and its
            # placement picks the partition from the resource shape.
            resources["walltime"] = _walltime(timeout_s + 900)
        task = {
            # The command bootstraps with the ABA controller python (`sys.executable`,
            # an absolute path valid on every node via the deployment's shared FS) so
            # `-m core.jobs.slurm_entry` runs with ABA's own deps; PYTHONPATH makes it
            # importable. When `env=<EnvID>` is set, weft ACTIVATES that env around the
            # whole command (PATH + CONDA_PREFIX point at the mounted prefix) — so the
            # entry runs the USER's code under the env's python (resolved from
            # CONDA_PREFIX, strategy-blind) while ABA's entry code stays on the
            # controller python. Nextflow heads carry no env (bare: `module load …`).
            "command": f"{sys.executable} -u -m core.jobs.slurm_entry {spec_path}",
            "site": self.site,
            "env_vars": {"PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                         **_aba_env_vars()},
            "resources": resources,
            "label": (job.get("title") or job["id"])[:200],
        }
        if env_id:
            task["env"] = env_id
        r = _adapter().sync_call("task_submit", task)
        from core.graph.jobs import update_job
        update_job(job["id"], params={**params, "weft_id": r["job_id"],
                                      "submitter": "weft", "weft_site": self.site},
                   project_id=str(pid))

    # ── detached transport (misc/detached_compute.md) ─────────────────────
    def _site_kind(self, site: Optional[str] = None) -> Optional[str]:
        name = site or self.site
        for s in declared_compute_sites():
            if s["name"] == name:
                return s.get("kind")
        return None

    def _detached_env(self, params: dict, pid: str, lang: str):
        """(env_id, env_name) for a detached job — same identity rules as the
        shared lane. env_name is kept for the lazy platform re-lock (only
        NAMED envs re-lock; the project default env has no per-project spec
        handle here, so a platform mismatch on it advises an isolated env)."""
        if params.get("env"):
            from core.compute import named_envs
            row = named_envs.resolve(str(pid), params["env"])
            if row is None:
                print(f"[jobs.weft] unknown isolated env {params['env']!r} — "
                      f"detached job runs on the node's system runtime")
                return None, None
            return row["env_id"], params["env"]
        try:
            from core.compute import base_env, project_env
            base_env.require(lang)
            return project_env.snapshot(str(pid), lang), None
        except Exception:  # noqa: BLE001 — env-less, honestly graded
            return None, None

    def _build_detached_task(self, job: dict, params: dict,
                             env_id: Optional[str],
                             site: Optional[str] = None) -> dict:
        """Payload dir {harness, user script, spec+nonce} → CAS ref → the
        weft task dict. Idempotent (same payload → same ref); used by submit
        AND the poll-side platform-re-lock resubmit. `site` overrides
        self.site — the POLL LOOP's generic WeftSubmitter() is site='local',
        so a resubmit must use the JOB's recorded site, never self's (a
        re-locked job once bounced to 'local' this way — found live)."""
        import shutil as _shutil
        kind = job.get("kind") or "run_python"
        lang = "r" if kind == "run_r" else "python"
        run_dir = self._run_dir(job)
        payload = run_dir / "payload"
        payload.mkdir(parents=True, exist_ok=True)
        script = "user_code.R" if lang == "r" else "user_code.py"
        (payload / script).write_text(params.get("code", ""))
        _shutil.copyfile(Path(__file__).with_name("detached_entry.py"),
                         payload / "aba_entry.py")
        timeout_s = int(params.get("timeout_s") or 600)
        (payload / "spec.json").write_text(json.dumps({
            "interpreter": "Rscript" if lang == "r" else "python3",
            "script": script,
            # memo nonce: identical code must NOT collide into weft's task
            # memo — "Re-run as-is" would silently return the cached result
            "job_id": job["id"],
            # enforced BY THE HARNESS on the node — ssh sites have no
            # scheduler walltime, so this is the only wall enforcement there
            "timeout_s": timeout_s,
        }))
        ref = _adapter().sync_call("data_register", str(payload),
                                   ingest=True)["ref"]
        # The CAS copy (ingest=True) is the payload of record — remove the
        # staging dir so it never surfaces as a Run output: it lives inside
        # the run dir, which the harvest sweep (*.json) and the Files panel
        # both read, and spec.json is internal (job-id memo nonce).
        _shutil.rmtree(payload, ignore_errors=True)
        est = params.get("estimate") or {}
        resources = {"cpus": int(est.get("cores") or 1)}
        if est.get("mem_gb"):
            resources["mem_gb"] = int(est["mem_gb"])
        if est.get("gpu"):
            resources["gpus"] = 1
        site = site or self.site
        if self._site_kind(site) == "slurm" and float(est.get("runtime_min") or 0) > 0:
            # Explicit walltime ONLY for a job the agent actually SIZED. An
            # unsized ask inflated from the default timeout pends FOREVER on
            # sites whose partition cap is below it (PartitionTimeLimit —
            # verified live on the 1h-cap fixture); omitting lets the
            # partition default apply, which runs. Weft doesn't refuse the
            # over-cap ask upfront (noted as a weft follow-up).
            resources["walltime"] = _walltime(timeout_s + 300)
        task = {
            # `python3` resolves from PATH — the activated env's prefix when
            # env= rides along (weft mounts it first), else the node system.
            # NO controller paths, NO ABA_* env — the node shares nothing.
            "command": "python3 payload/aba_entry.py",
            "site": site,
            "inputs": [{"ref": ref, "mount_as": "payload"}],
            "resources": resources,
            "label": (job.get("title") or job["id"])[:200],
        }
        if env_id:
            task["env"] = env_id
        return task

    def _submit_detached(self, job: dict) -> None:
        """Code travels AS DATA (misc/detached_compute.md): payload {harness,
        user script, spec+nonce} → CAS ref → weft stages it into the task
        workdir on the node. The harness runs the script under the env's
        interpreter (weft realizes EnvID at the site) or the node system, and
        writes result.json there; poll() reads it back over the data plane."""
        from core.compute.errors import ComputeError
        params = job.get("params") or {}
        pid = params.get("project_id") or "default"
        kind = job.get("kind") or "run_python"
        lang = "r" if kind == "run_r" else "python"
        comp = _adapter()
        env_id, env_name = self._detached_env(params, pid, lang)
        task = self._build_detached_task(job, params, env_id)
        try:
            r = comp.sync_call("task_submit", task)
        except ComputeError as e:
            plat = _mismatch_platform(e)
            if plat and env_name:
                # lazy re-lock (design §Environments): add the site's platform
                # to the env's lock and retry ONCE
                from core.compute import named_envs
                relock = named_envs.ensure_platform(str(pid), env_name, plat)
                task["env"] = env_id = relock["env_id"]
                r = comp.sync_call("task_submit", task)
            elif plat:
                raise ComputeError(
                    code="env.platform_mismatch",
                    detail=f"the project env is locked for this machine only; "
                           f"site {self.site} needs platform {plat}. Use an "
                           f"isolated env (make_isolated_env) for remote jobs — "
                           f"it re-locks automatically.") from e
            else:
                raise
        from core.graph.jobs import update_job
        env_note = ({"env_id": env_id} if env_id
                    else {"env_grade": "node-system"})
        update_job(job["id"], params={**params, "weft_id": r["job_id"],
                                      "submitter": "weft",
                                      "weft_site": self.site,
                                      "detached": True, **env_note},
                   project_id=str(pid))
        # the one line that lights up retention + status UI for remote outputs:
        # the Run's durable panel, keep-triage, (run,rel) addressing and
        # bring-back are all target-based — give them the target
        self._record_run_target(params, r["job_id"])

    @staticmethod
    def _record_run_target(params: dict, new_wid: str,
                           replace: Optional[str] = None) -> None:
        """Record a weft task id on the owning Run's `weft_targets` — the
        SINGLE bookkeeping both submit and the poll-side re-lock resubmit go
        through. `replace` drops a DEAD prior attempt (a platform-mismatch
        task has no files; leaving it made retention + the durable view aim
        at nothing — found by the live study)."""
        run_id = params.get("run_id")
        if not run_id:
            return
        try:
            from core.graph.entities import get_entity, update_entity
            ent = get_entity(run_id)
            if not ent:
                return
            md = dict(ent.get("metadata") or {})
            tgts = [t for t in (md.get("weft_targets") or []) if t != replace]
            if new_wid not in tgts:
                md["weft_targets"] = tgts + [new_wid]
                update_entity(run_id, metadata=md)
        except Exception:  # noqa: BLE001 — panel misses the target, job unaffected
            pass

    def _poll_detached(self, job: dict, params: dict, wid: str, state: str) -> dict:
        """Terminal detached task → result-shaped dict for _finalize_job.
        result.json + small produced files come back over the weft data
        plane; harvest runs CONTROLLER-side so figures/tables enter the
        standard pipeline. Large files stay on the node (kept per close-run
        policy; (run,rel)-addressable; bring-back)."""
        import base64
        from core.compute import retention
        if state == "CANCELLED":
            res: dict = {"status": "cancelled",
                         "note": "cancelled on the compute substrate"}
            res.setdefault("compute", self._compute_block(wid, state))
            return res

        def _read(rel: str, cap: int = _DETACHED_FETCH_BYTES):
            try:
                out = retention.file_read(wid, rel, max_bytes=cap)
                if out.get("truncated"):
                    return None
                return base64.b64decode(out.get("bytes_b64") or "")
            except Exception:  # noqa: BLE001
                return None
        raw = _read("result.json", cap=1 << 20)
        if raw is None:
            task_err = None
            if state == "FAILED":
                try:
                    rows = _adapter().sync_call("task_status", wid)
                    task_err = (rows[0] or {}).get("error")
                except Exception:  # noqa: BLE001
                    pass
            # Lazy platform re-lock, POLL side: this weft surfaces
            # env.platform_mismatch at realize (async), so the submit-time
            # catch never sees it. Re-lock for the site's platform and
            # RESUBMIT transparently — once. NAMED envs re-solve their
            # recorded spec; the DEFAULT env re-locks its BASE PACK (the
            # session snapshot's extras don't travel — recorded on the job).
            plat = _row_mismatch_platform(task_err)
            if plat and not params.get("platform_relocked") and \
                    (params.get("env") or params.get("env_id")):
                try:
                    pid = params.get("project_id") or "default"
                    extra: dict = {}
                    if params.get("env"):
                        from core.compute import named_envs
                        relock = named_envs.ensure_platform(
                            str(pid), params["env"], plat)
                    else:
                        from core.compute import base_env
                        lang = "r" if (job.get("kind") == "run_r") else "python"
                        relock = base_env.ensure_platform(lang, plat)
                        extra["env_note"] = (
                            "re-locked BASE pack for the site platform — "
                            "session-installed extras are not in this env")
                    job_site = params.get("weft_site") or params.get("site") \
                        or self.site
                    task = self._build_detached_task(job, params,
                                                     relock["env_id"],
                                                     site=job_site)
                    r = _adapter().sync_call("task_submit", task)
                    from core.graph.jobs import update_job
                    update_job(job["id"],
                               params={**params, "weft_id": r["job_id"],
                                       "env_id": relock["env_id"],
                                       "platform_relocked": True, **extra},
                               project_id=str(pid))
                    # repoint the Run at the LIVE task — the dead mismatch
                    # attempt has no files; retention + the durable view
                    # would otherwise aim at nothing (live-study finding)
                    self._record_run_target(params, r["job_id"], replace=wid)
                    print(f"[jobs.weft] env re-locked for {plat} and job "
                          f"{job['id']} resubmitted to {job_site}")
                    return None            # keep polling the NEW task
                except Exception as e:  # noqa: BLE001 — fall through to failure
                    print(f"[jobs.weft] platform re-lock failed: {e}")
            res = {"error": f"weft task {state} before the harness could run"}
            if task_err:
                from core.data.datasets import explain_data_error
                friendly = explain_data_error(task_err)
                if friendly:
                    res["error"] = friendly
                    res["error_detail"] = str(task_err)
                elif plat:
                    res["error"] = (
                        f"this env is not available for {self.site}'s platform "
                        f"({plat}) — the re-lock failed or wasn't possible; "
                        f"see error_detail")
                    res["error_detail"] = str(task_err)
                else:
                    res["error"] = f"the compute substrate reported: {task_err}"
            comp = self._compute_block(wid, state)
            if comp.get("log_tail") and "substrate reported" not in res["error"]:
                res["error"] += f"\n--- node log tail ---\n{comp['log_tail'][-400:]}"
            res.setdefault("compute", comp)
            return res
        try:
            node = json.loads(raw)
        except Exception:  # noqa: BLE001
            node = {"status": "error", "returncode": 1,
                    "error": "result.json unreadable"}
        run_dir = self._run_dir(job)
        fetched = 0
        for rel in (node.get("outputs") or [])[:200]:
            try:
                st = retention.file_stat(wid, rel)
                if not st.get("exists") or (st.get("bytes") or 0) > _DETACHED_FETCH_BYTES:
                    continue
                data = _read(rel)
                if data is None:
                    continue
                dest = run_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                fetched += 1
            except Exception:  # noqa: BLE001 — a skipped file stays node-side, kept
                continue
        res = {"status": node.get("status") or "ok",
               "returncode": node.get("returncode", 0),
               "stdout": node.get("stdout_tail") or "",
               "stderr": ""}
        if node.get("status") == "error":
            res["error"] = node.get("error") or "job failed on the node"
        elif fetched:
            try:
                from core.exec.run import harvest_artifacts
                plots, tables, files, warnings = harvest_artifacts(
                    run_dir, since_ts=0.0,
                    project_id=params.get("project_id"))
                res.update({"plots": plots, "tables": tables,
                            "files": files, "warnings": warnings})
            except Exception:  # noqa: BLE001 — files still in run dir/Files panel
                pass
        comp = self._compute_block(wid, state)
        if params.get("env_id"):
            comp["env_id"] = params["env_id"]
        else:
            comp["env_grade"] = "node-system"
            if node.get("runtime"):
                comp["runtime"] = node["runtime"]
        res.setdefault("compute", comp)
        return res

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
        if params.get("detached"):
            # detached transport: results come back over the weft data plane,
            # not a shared filesystem (misc/detached_compute.md)
            return self._poll_detached(job, params, wid, state)
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
            # A Nextflow HEAD that died at the scheduler level (walltime/node-fail/
            # preempt) with no result.json is auto-resumable — mark it so the poll
            # loop re-submits with -resume (runner._maybe_resume_nextflow_job). The
            # field name is legacy (it triggered off the retired sbatch lane); it is
            # now the generic infra-terminal-death signal, set by whichever lane runs
            # the head — which is this one.
            if job.get("kind") == "run_nextflow":
                res["slurm_terminal_fail"] = state
        if state == "FAILED":
            # data-plane failures get the plain-language translation
            # (misc/datasets2.md S3): staging is async, so a drifted/vanished
            # durable home lands HERE as a failed job, not at submit
            try:
                from core.data.datasets import explain_data_error
                friendly = explain_data_error((rows[0] or {}).get("error"))
                if friendly:
                    res["error"] = friendly
                    res["error_detail"] = (rows[0] or {}).get("error")
            except Exception:  # noqa: BLE001 — translation must never mask the failure
                pass
        comp = self._compute_block(wid, state)
        # the entry copies spec env_id into the result — a bare task's weft
        # manifest has none, but the SNAPSHOT identity is real (W3.4)
        if isinstance(res, dict) and res.get("env_id"):
            comp["env_id"] = res["env_id"]
        res.setdefault("compute", comp)
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
