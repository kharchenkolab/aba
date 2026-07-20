"""Environment diagnostics + filesystem self-checks (env_refactor.md P0 remnant).

The honest import/GPU load-verification primitives moved to ``core.exec.verify``
(W3.4); the served-base heal/repair/lock machinery was deleted with the served
base itself (W3.5 — weft owns environment realization now). What remains is
read-only: per-package + layered env diagnostics for the (i)-drawer Env tab, the
``ensure_sys_executable`` startup recovery, the base-stage marker read, and the
Slurm shared-FS self-checks (a node-local ENVS_DIR/base is unreachable by a job
offloaded to another node).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from core import config


def python_package_status(name: str, *, project_id: Optional[str] = None,
                          extra_paths: Optional[Sequence[str]] = None,
                          timeout_s: int = 120) -> dict:
    """Diagnose one Python package in the project's weft SESSION:
    ``{name, loads, version, location, tier, error}``. ``loads=False`` with a
    populated ``error`` is the present-but-broken case (ABI mismatch / partial
    install) — the troubleshooting signal the agent needs."""
    out: dict = {"name": name, "loads": False, "version": None,
                 "location": None, "tier": "unknown", "error": None}
    if not name:
        out["error"] = "no name"
        return out
    if project_id is None:
        from core import projects
        project_id = projects.current()
    appends = "".join(f"sys.path.append({str(p)!r})\n" for p in (extra_paths or []))
    script = (
        "import sys, json, importlib\n"
        "import importlib.metadata as _md\n"
        f"{appends}"
        f"o = {{'name': {name!r}}}\n"
        "try:\n"
        f"    m = importlib.import_module({name!r})\n"
        "    o['loads'] = True\n"
        "    o['location'] = getattr(m, '__file__', None)\n"
        "    try:\n"
        f"        o['version'] = _md.version({name!r})\n"
        "    except Exception:\n"
        "        o['version'] = getattr(m, '__version__', None)\n"
        "except Exception:\n"
        "    import traceback\n"
        "    o['loads'] = False\n"
        "    o['error'] = traceback.format_exc()[-1000:]\n"
        "print('ABA_JSON=' + json.dumps(o))\n"
    )
    # Probe the project's weft SESSION python (its site-packages are
    # authoritative) via the topology-blind argv builder — a lazy session's
    # probe runs against its base realization (content-identical), a
    # mount-scoped one through its activation. Fall back to the aba runtime
    # interpreter when no python pack is declared.
    probe_argv = [sys.executable, "-c", script]
    from_session = False
    try:
        from core.compute import base_env as _bev, project_env as _penv
        if _bev.active("python"):
            probe_argv = _penv.exec_argv(str(project_id or "_none"), "python",
                                         ["-c", script])
            from_session = True
    except Exception:  # noqa: BLE001 — no realizable session → runtime interpreter
        pass
    try:
        proc = subprocess.run(probe_argv,
                              capture_output=True, text=True, timeout=timeout_s)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"could not run diagnostic: {e}"
        return out
    import json as _json
    for ln in (proc.stdout or "").splitlines():
        if ln.startswith("ABA_JSON="):
            try:
                out.update(_json.loads(ln[len("ABA_JSON="):]))
            except Exception:  # noqa: BLE001
                pass
            break
    out["tier"] = (("session" if from_session else "base")
                   if out.get("loads") else "unknown")
    return out


def env_overview(project_id: Optional[str] = None) -> dict:
    """A map of the Python env — the no-package 'where am I' view: the project's
    weft SESSION python (base pack + session_install additions) and the aba
    runtime interpreter. The session block reports the RUNTIME truth — a lazy
    session that runs from its base realization is `active` with
    `materialized: False` (the old prefix-derived `active` read a healthy lazy
    session as absent), and a mutated session's identity is honestly "unhashed
    scratch" until snapshot."""
    from core.compute import base_env as _bev, project_env as _penv
    if project_id is None:
        from core import projects
        project_id = projects.current()
    session: dict = {"project_id": project_id, "active": False, "prefix": None,
                     "materialized": None, "source": None, "identity": None}
    if project_id and _bev.active("python"):
        try:
            info = _penv.ensure(str(project_id), "python")
            rt = info["runtime"]
            session.update({
                "active": True,
                "prefix": str(info["prefix"]) if info["prefix"] else None,
                "materialized": info["materialized"],
                "source": rt.get("source"),
                # weft contract: env_id is NULL once mutated (a clone OR a
                # cold-base pylib overlay) — scratch has no identity until
                # snapshot; unmutated sessions carry the base's identity
                "identity": (rt.get("env_id") or
                             "unhashed scratch — snapshot before recording results"),
            })
            _ovl = {k: rt[k] for k in ("pylib", "rlib") if rt.get(k)}
            if _ovl:
                session["overlays"] = _ovl   # additive layers riding the base
        except Exception:  # noqa: BLE001 — session not realizable
            pass
    return {
        "python": sys.executable,          # the aba runtime interpreter
        "session": session,
    }


def _py_packages(site_dirs: Sequence) -> list[dict]:
    """`{name, version}` for every distribution in the given site-packages
    dir(s), deduped, sorted — by reading dist-info (no subprocess)."""
    import importlib.metadata as md
    out: dict = {}
    for d in site_dirs:
        p = Path(d)
        if not p.exists():
            continue
        try:
            dists = list(md.distributions(path=[str(p)]))
        except Exception:  # noqa: BLE001
            continue
        for dist in dists:
            try:
                name = dist.metadata["Name"]
                ver = dist.version
            except Exception:  # noqa: BLE001
                continue
            if name:
                out[name.lower()] = {"name": name, "version": ver}
    return sorted(out.values(), key=lambda x: x["name"].lower())


def _r_packages_by_lib(lib_paths: Sequence, rscript: Optional[str] = None) -> dict:
    """One Rscript (the weft R SESSION's Rscript): installed.packages() grouped by
    LibPath, with the given libs prepended to .libPaths(). Returns
    {realpath(lib): [{name,version}]}. No rscript (no R pack) → {}."""
    import os
    import subprocess
    paths = [str(p) for p in lib_paths if p]
    if not paths or not rscript:
        return {}
    libs_r = "c(" + ", ".join(repr(p) for p in paths) + ")"
    expr = (f"libs <- {libs_r}; .libPaths(c(libs, .libPaths())); "
            f"ip <- installed.packages(); "
            f"if (nrow(ip)>0) for (i in seq_len(nrow(ip))) "
            f"cat('PKG\\t', ip[i,'LibPath'], '\\t', ip[i,'Package'], '\\t', ip[i,'Version'], '\\n', sep='')")
    try:
        proc = subprocess.run([rscript, "-e", expr], capture_output=True,
                              text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return {}
    by: dict = {}
    for ln in (proc.stdout or "").splitlines():
        if not ln.startswith("PKG\t"):
            continue
        parts = ln.split("\t")
        if len(parts) >= 4:
            try:
                lp = os.path.realpath(parts[1].strip())
            except Exception:  # noqa: BLE001
                lp = parts[1].strip()
            by.setdefault(lp, []).append({"name": parts[2].strip(), "version": parts[3].strip()})
    return by


def env_layers(project_id: Optional[str] = None) -> dict:
    """The layered Python + R environments with their packages — the data behind
    the (i) drawer's Env tab. Python via dist-info scan (fast); R via one
    Rscript. Each layer: {tier, scope, delivery, mutable, path, packages}."""
    import os
    from core.compute import named_envs
    from core.compute import base_env as _bev, project_env as _penv
    from core.exec.materialize import _site_paths
    if project_id is None:
        from core import projects
        project_id = projects.current()
    iso_names = named_envs.list_names(str(project_id)) if project_id else []

    # ── Python ── the weft python SESSION (base pack + session_install additions)
    # is the env; named/isolated weft envs stack on top. No project / no pack →
    # no session tier. (The served-base venv + pip overlay are gone — W3.5.)
    py_layers = []
    if project_id and _bev.active("python"):
        try:
            _info = _penv.ensure(str(project_id), "python")
            _rt = _info["runtime"]
            if _rt.get("pylib"):
                # cold-base session: the session's own layer is a PYLIB overlay
                # over the mounted base (a flat --target dir; dist-info scans
                # directly) — the base itself is below it, read-only
                py_layers.append(
                    {"tier": "session", "scope": "project", "project_id": project_id,
                     "delivery": "weft", "mutable": True, "mode": "pylib-overlay",
                     "path": str(_rt["pylib"]),
                     "packages": _py_packages([str(_rt["pylib"])])})
            elif _rt.get("direct_exec") and _info["prefix"] is not None:
                py_sess = _info["prefix"]
                py_layers.append(
                    {"tier": "session", "scope": "project", "project_id": project_id,
                     "delivery": "weft", "mutable": True, "path": str(py_sess),
                     "packages": _py_packages([str(p) for p in _site_paths(py_sess)])})
            # else: activation-only with no scannable dir — no session layer
            # (the runtime truth still shows in env_overview)
        except Exception:  # noqa: BLE001 — session not realizable → no session layer
            pass
    for name in iso_names:
        row = named_envs.resolve(str(project_id), name) or {}
        if row.get("language") == "r":
            continue
        py_layers.append(
            {"tier": "isolated", "scope": "capability", "delivery": "weft",
             "mutable": False,   # frozen EnvID — additions layer to a NEW id
             "name": name, "env_id": row.get("env_id"),
             "packages": [{"name": p, "version": ""} for p in row.get("packages", [])]})
    py = {"engine": "weft python session + isolated envs", "layers": py_layers}

    # ── R ── the weft R SESSION (base pack + additions) is the R env; named/
    # isolated weft R envs stack on top. No project / no R pack → no session layer.
    r_layers = []
    from core.compute import base_env as _bev, project_env as _penv
    if project_id and _bev.active("r"):
        try:
            _rinfo = _penv.ensure(str(project_id), "r")
            _rrt = _rinfo["runtime"]
            if _rrt.get("rlib"):
                # cran layer riding the base (weft 80e609d): a session-owned
                # R_LIBS dir — package dirs enumerate directly, no Rscript
                _rl = Path(_rrt["rlib"])
                _names = (sorted(p.name for p in _rl.iterdir() if p.is_dir())
                          if _rl.exists() else [])
                r_layers.append({"tier": "session", "scope": "project",
                                 "project_id": project_id, "delivery": "weft",
                                 "mutable": True, "mode": "rlib-overlay",
                                 "path": str(_rl),
                                 "packages": [{"name": n, "version": ""} for n in _names]})
            elif _rrt.get("direct_exec") and _rinfo["prefix"] is not None:
                r_sess = _rinfo["prefix"]
                r_sess_lib = r_sess / "lib" / "R" / "library"
                r_rscript = str(r_sess / "bin" / "Rscript")
                by = _r_packages_by_lib([r_sess_lib], rscript=r_rscript)
                r_layers.append({"tier": "session", "scope": "project", "project_id": project_id,
                                 "delivery": "weft", "mutable": True, "path": str(r_sess_lib),
                                 "packages": by.get(os.path.realpath(str(r_sess_lib)), [])})
            # else: activation-only with no scannable dir — no session layer
        except Exception:  # noqa: BLE001 — R session not realizable → no session layer
            pass
    # Named (weft) R envs — full standalone envs, rendered from the handle.
    for name in iso_names:
        row = named_envs.resolve(str(project_id), name) or {}
        if row.get("language") == "r":
            r_layers.append({"tier": "isolated", "scope": "capability", "delivery": "weft",
                             "mutable": False, "name": name, "env_id": row.get("env_id"),
                             "packages": [{"name": p, "version": ""}
                                          for p in row.get("packages", [])]})
    r = {"engine": "weft R session + isolated envs", "layers": r_layers}

    return {"python": py, "r": r, "project_id": project_id}


def _base_site_dir() -> Path:
    import sysconfig
    return Path(sysconfig.get_path("purelib"))


def _base_prefix() -> Path:
    """Base env prefix ($ABA_HOME/env): purelib is <prefix>/lib/pythonX.Y/site-packages."""
    return _base_site_dir().parents[2]


def base_stage() -> str:
    """Install-time base-build stage from the `.aba-base-stage` marker (written by
    create-env + the backend python-bio module completion under ABA_ENV_PREWARM=staged):
    'boot' (minimal base, server started) | 'completing' (full stack installing) |
    'ready'. Absent ⇒ 'ready' — eager builds and every pre-staging install. Lets the
    kernel path wait on a completing base instead of erroring on a not-yet-installed
    import (lazy_env_init.md)."""
    try:
        m = _base_prefix() / ".aba-base-stage"
        if m.exists():
            v = m.read_text().strip()
            if v in ("boot", "completing", "ready"):
                return v
    except Exception:  # noqa: BLE001
        pass
    return "ready"


def ensure_sys_executable() -> str:
    """Recover ``sys.executable`` when it is '' (empty).

    Launching the server via ``os.execve(py, ["python", ...])`` with a BARE
    argv[0] (not an absolute path) leaves the interpreter unable to locate itself,
    so ``sys.executable`` becomes ''. That empty string then silently poisons
    EVERY subprocess that falls back to it — the base self-heal's pip, run_python's
    interpreter, capability materialize — each surfacing as the cryptic
    ``PermissionError: [Errno 13] Permission denied: ''`` (live incident
    2026-06-28, prj_0590c5d8). Resolve the real interpreter from
    ``sys._base_executable`` or the venv layout and patch it back into
    ``sys.executable`` process-wide. Idempotent; returns the resolved path."""
    if sys.executable:
        return sys.executable
    import os
    for cand in (getattr(sys, "_base_executable", "") or "",
                 os.path.join(sys.prefix, "bin", "python3"),
                 os.path.join(sys.prefix, "bin", "python")):
        if cand and os.path.exists(cand):
            sys.executable = cand
            print(f"[env] recovered empty sys.executable -> {cand}", flush=True)
            return cand
    return sys.executable


# ─── ENVS_DIR must be shared-FS under Slurm (finding F6b, HIGH) ────────────────
# A package ensure_capability'd into ENVS_DIR/pylib is added to every run's
# sys.path. Under a Slurm submitter the run happens on ANOTHER node, so if
# ENVS_DIR is node-local the job dies on ModuleNotFoundError with no obvious
# cause. We classify by ACTUAL mount fstype (not path prefix), so the default
# `/workspace` trap and non-standard local mounts are caught too.
_SHARED_FS = {"nfs", "nfs4", "lustre", "gpfs", "beegfs", "beegfs_nodev", "fhgfs",
              "cephfs", "ceph", "glusterfs", "fuse.glusterfs", "smb3", "cifs",
              "panfs", "pvfs2", "orangefs", "9p", "afs"}
# `overlay`/`squashfs` matter under a fat SIF: apptainer preserves a bind's real
# fstype in the container's mountinfo (a shared NFS/beegfs bind reads as nfs/beegfs),
# but an ENVS_DIR that falls INSIDE the read-only image (its session overlay /
# squashfs lowerdir) is node-local + ephemeral — correctly flagged (verified on a SIF).
_LOCAL_FS = {"tmpfs", "ramfs", "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs",
             "reiserfs", "jfs", "vfat", "devtmpfs", "overlay", "squashfs", "fuse.squashfuse"}


def _fs_type_for_path(path) -> "str | None":
    """Filesystem type backing ``path`` via /proc/self/mountinfo (longest
    mount-point-prefix match). None if unreadable (non-Linux / no procfs)."""
    try:
        real = os.path.realpath(str(path))
        best_mp, best_fstype = "", None
        with open("/proc/self/mountinfo") as f:
            for line in f:
                try:
                    pre, post = line.split(" - ", 1)
                    mp = pre.split()[4]                 # mount point
                    fstype = post.split()[0]            # fs type (after " - ")
                except (ValueError, IndexError):
                    continue
                if (real == mp or real.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best_mp):
                    best_mp, best_fstype = mp, fstype
        return best_fstype
    except Exception:  # noqa: BLE001
        return None


def _classify_fs(path) -> "tuple[str, str]":
    """``(kind, detail)`` for a path's backing filesystem — shared|node_local|unknown,
    by actual mount fstype. NB `overlay`/`squashfs` (a fat SIF's in-image session FS)
    count as **node-local**: reachable only INSIDE the container, and a Slurm `job.sh`
    runs BARE on the compute node (no `apptainer exec` re-entry — slurm_submitter.py)."""
    p = str(path)
    fstype = _fs_type_for_path(p)
    if fstype is None:
        return "unknown", f"could not determine fs type for {p}"
    if fstype in _SHARED_FS:
        return "shared", f"{p} on {fstype} (shared)"
    if fstype in _LOCAL_FS:
        return "node_local", f"{p} on {fstype} (node-local / in-image)"
    return "unknown", f"{p} on {fstype} (fs type not classified)"


def envs_dir_fs_kind() -> "tuple[str, str]":
    """``(kind, detail)`` for the filesystem under ENVS_DIR (the growth overlay).
    Empirical (mount fstype), so it catches the `/workspace`-node-local trap a
    path-prefix check misses."""
    from core.exec.materialize import ENVS_DIR
    return _classify_fs(str(ENVS_DIR))


def base_fs_kind() -> "tuple[str, str]":
    """``(kind, detail)`` for the filesystem under the BASE venv (`sysconfig` purelib).
    Fat SIF → the in-image overlay/squashfs → node_local; slim → the `image.base_dir`
    bind (shared iff pointed at shared FS); native → the install FS."""
    return _classify_fs(str(_base_site_dir()))


def _on_slurm() -> bool:
    """True when ABA itself runs inside a Slurm allocation (SLURM_JOB_ID set). Then
    in-allocation jobs run INLINE (this process/container), so a node-local/in-image
    ENVS_DIR or base is reachable for THEM; only jobs offloaded BEYOND the allocation
    (sbatched to another node) can't reach it → a warning, not a hard 'high'. On a
    bare submit node (no allocation), every job offloads → 'high'."""
    return bool(os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID"))


def check_envs_dir_shared() -> dict:
    """Self-check (see selfcheck.py): under a Slurm submitter ENVS_DIR must be on
    shared storage. Fires only for the 'slurm' submitter — a local submitter runs
    jobs on this same node, so node-local is fine. Severity is `on_slurm`-aware
    (inline covers in-allocation jobs; only true offload fails)."""
    from core.jobs.submitter import submitter_name
    if submitter_name() != "slurm":
        return {"ok": True, "severity": "info", "detail": "local submitter — ENVS_DIR sharing N/A"}
    kind, detail = envs_dir_fs_kind()
    if kind == "shared":
        return {"ok": True, "severity": "info", "detail": detail}
    if kind == "node_local":
        if _on_slurm():
            return {"ok": False, "severity": "warning",
                    "detail": (f"ENVS_DIR is node-local ({detail}); in-allocation jobs run inline so they "
                               "work, but a job offloaded to ANOTHER node can't see ensure_capability'd "
                               "packages. Point ABA_RUNTIME_DIR/ABA_ENVS_DIR at shared storage for true offload.")}
        return {"ok": False, "severity": "high",
                "detail": (f"ENVS_DIR is node-local ({detail}) and this is a submit node (no allocation), so "
                           "every background job runs on another node and can't see ensure_capability'd "
                           "packages. Point ABA_RUNTIME_DIR/ABA_ENVS_DIR at shared storage.")}
    return {"ok": False, "severity": "warning",
            "detail": (f"ENVS_DIR shared-ness unverified ({detail}); if node-local, offloaded Slurm "
                       "jobs will fail to import provisioned packages. Confirm shared storage or run "
                       "the install-time probe (aba doctor).")}


def check_base_dir_shared() -> dict:
    """Self-check: under a Slurm submitter the BASE venv must be REACHABLE by an
    offloaded job. How it's reached depends on the delivery mode:

    - BARE offload (native / slim — the default): the generated job.sh runs the
      interpreter directly on the compute node (`sys.executable -u -m
      core.jobs.slurm_entry`, no container re-entry), so the base MUST be on shared
      FS — a slim SIF (`image.base_dir` on shared FS) or a native shared install. An
      in-image / node-local base is unreachable → the job can't even find the
      interpreter.
    - WRAPPED offload (`ABA_JOB_WRAP=sif`, a fat OR weft SIF): the job RE-ENTERS the
      image via `apptainer exec` (slurm_submitter._job_body), so the baked in-image
      base is exactly what runs — a node-local/in-image base is CORRECT, not a defect,
      and NOT single-node (misc/fatagain.md). (Under the weft profile the baked base is
      only the slim controller runtime; the science env is a weft image adopted on the
      node — either way the offloaded job reaches its interpreter.)

    Fires only for the 'slurm' submitter."""
    from core.jobs.submitter import submitter_name
    if submitter_name() != "slurm":
        return {"ok": True, "severity": "info", "detail": "local submitter — base sharing N/A"}
    # Job-wrap (fat or weft SIF): offloaded jobs re-enter the SIF, so the baked base is
    # reachable (its being node-local/in-image is by design). Don't flag it as unreachable.
    if (config.settings.job_wrap.get() or "").strip().lower() == "sif":
        return {"ok": True, "severity": "info",
                "detail": ("SIF + job-wrap (ABA_JOB_WRAP=sif): offloaded jobs re-enter the "
                           "image via `apptainer exec`, so the baked in-image base is reachable — "
                           "not single-node (misc/fatagain.md).")}
    kind, detail = base_fs_kind()
    if kind == "shared":
        return {"ok": True, "severity": "info", "detail": detail}
    if kind == "node_local":
        if _on_slurm():
            return {"ok": False, "severity": "warning",
                    "detail": (f"base venv is node-local / in-image ({detail}); in-allocation jobs run inline "
                               "in this container so they work, but a job offloaded to ANOTHER node can't reach "
                               "the baked base (bare job.sh, no container re-entry). For true offload use a slim "
                               "SIF (image.base_dir on shared FS) or a native shared install; a fat SIF is "
                               "inline / single-node.")}
        return {"ok": False, "severity": "high",
                "detail": (f"base venv is node-local / in-image ({detail}) and this is a submit node (no "
                           "allocation), so every background job runs bare on another node and can't reach it. "
                           "Use a slim SIF (image.base_dir on shared FS) or a native shared install.")}
    return {"ok": False, "severity": "warning",
            "detail": f"base venv shared-ness unverified ({detail}); confirm it is on shared storage."}
