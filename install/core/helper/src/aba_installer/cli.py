"""Headless installer CLI — drives the playbook directly (no HTTP/UI).

For Linux servers + login nodes, the SIF build, and OOD — anywhere there's no
browser and no Tier-0 agent. Streams progress to stdout and, on a failed step,
prints that step's `remediation` (the no-agent robustness path). Invoked by
`install/linux/setup.sh` and by the `aba` launcher's `update` / `doctor`
subcommands.

  python -m aba_installer.cli install [--only a,b] [--skip start-backend]
  python -m aba_installer.cli update
  python -m aba_installer.cli doctor
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from aba_installer.playbook import Executor, load_playbook


def _ensure_aba_home() -> None:
    """Make ABA_HOME present in the environment so the playbook's
    $ABA_HOME/env, $ABA_HOME/repo expansions resolve (os.path.expandvars reads
    os.environ). Respects an existing export — a custom location wins."""
    os.environ.setdefault("ABA_HOME", os.path.expanduser("~/.aba"))


def _emit(name: str, payload: dict) -> None:
    if name == "step_start":
        print(f"\n▶ {payload.get('title') or payload['step_id']}", flush=True)
    elif name == "command_output":
        line = payload.get("line", "")
        if line.strip():
            print(f"  {line}", flush=True)
    elif name == "step_end":
        if payload.get("ok"):
            print(f"  ✓ {payload['step_id']}", flush=True)
        else:
            print(f"  ✗ {payload['step_id']} FAILED: {payload.get('error')}", flush=True)
            rem = (payload.get("remediation") or "").strip()
            if rem:
                print("\n  How to fix:", flush=True)
                for ln in rem.splitlines():
                    print(f"    {ln}", flush=True)


def _bootstrap_repo_for_update() -> None:
    """Bring the deployed repo up to date BEFORE loading the update playbook, so a
    step (or a fix to the playbook/installer) added upstream takes effect on THIS
    `aba update`, not the next one. Mirrors the in-playbook pull-aba; pull-aba then
    re-runs idempotently. Two source modes, matching pull-aba:
      • ABA_REPO_SRC set (local / offline / dev) → rsync from that checkout.
      • else a git checkout → shallow-fetch $ABA_REF and reset.
    Non-fatal: on any failure we fall through and `_playbook_path` uses the on-disk
    repo copy, else the bundled one."""
    repo = Path(os.environ.get("ABA_HOME") or (Path.home() / ".aba")) / "repo" / "aba"
    src = os.environ.get("ABA_REPO_SRC")
    if src:
        # local/offline/dev install: pre-sync from the source checkout (same rsync +
        # excludes as pull-aba) so we load THIS release's playbook, not the stale
        # deployed copy — the manual-pre-sync trap the git path already avoids.
        if not repo.is_dir():
            return
        try:
            subprocess.run(
                ["rsync", "-a",
                 "--exclude", ".envs_cache", "--exclude", "_runs", "--exclude", "reports",
                 "--exclude", ".git", "--exclude", ".venv", "--exclude", "node_modules",
                 "--exclude", "dist", "--exclude", "__pycache__",
                 f"{src.rstrip('/')}/", f"{repo}/"],
                check=True, capture_output=True, timeout=300)
            print(f"  (synced repo from {src} so the current update playbook is used)", flush=True)
        except Exception:  # noqa: BLE001 — best-effort; _playbook_path falls back to on-disk/bundled
            pass
        return
    if not (repo / ".git").is_dir():
        return
    ref = os.environ.get("ABA_REF", "main")
    # Run git with cwd=repo, NOT `git -C repo`: the RHEL7 / CLIP cluster nodes ship
    # git 1.8.3.1, which has no `-C` flag (fails "Unknown option: -C") — and those are
    # exactly the nodes cluster-personal installs onto. cwd= is portable to any git.
    def _g(args, timeout):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, timeout=timeout)
    try:
        # shallow-fetch the ref (branch / tag / server-allowed SHA) → reset to it
        _g(["fetch", "--depth", "1", "origin", ref], 180)
        _g(["reset", "--hard", "FETCH_HEAD"], 60)
    except Exception:  # noqa: BLE001 — bare SHA / server rejects shallow ref-fetch
        try:
            _g(["fetch", "origin"], 300)
            _g(["reset", "--hard", ref], 60)
        except Exception:  # noqa: BLE001 — best-effort; playbook resolution still has fallbacks
            return
    print(f"  (refreshed repo → {ref} so the current update playbook is used)", flush=True)


def run_playbook_headless(name: str, *, only=None, skip=None) -> int:
    """Load + run a playbook synchronously, streaming to stdout. Returns 0 on
    full success, 1 if a step failed (it stops there, remediation printed)."""
    _ensure_aba_home()
    from aba_installer import control
    control.load_config_env()   # deploy knobs from config.env → playbook env (LaunchAgent-safe)
    if name == "install":
        control.prepare_install_artifacts()           # render the aba launcher template etc.
    if name == "update":
        _bootstrap_repo_for_update()                  # so we load THIS release's playbook, not last's
    pb = load_playbook(control._playbook_path(name))
    ids = [s.id for s in pb.steps]
    if skip:
        ids = [s for s in ids if s not in set(skip)]
    if only:
        ids = [s for s in ids if s in set(only)]
    if not ids:
        print(f"No steps to run (check --only/--skip). Steps: {', '.join(s.id for s in pb.steps)}", flush=True)
        return 1
    print(f"ABA {name}: {' -> '.join(ids)}", flush=True)
    results = Executor(pb, on_event=_emit).run_all(only=set(ids))
    ok = len(results) == len(ids) and all(r.ok for r in results)
    print(f"\n{'✓ ' + name + ' complete' if ok else '✗ ' + name + ' stopped on a failed step (see the fix above)'}", flush=True)
    return 0 if ok else 1


# Shared vs node-local filesystem classification for the Slurm provisioning-dir
# gate. Mirrors backend/core/exec/env_integrity._fs_type_for_path — duplicated
# (not imported) on purpose: the installer package is standalone (no backend dep).
_SHARED_FS = {"nfs", "nfs4", "lustre", "gpfs", "beegfs", "beegfs_nodev", "fhgfs",
              "cephfs", "ceph", "glusterfs", "fuse.glusterfs", "smb3", "cifs",
              "panfs", "pvfs2", "orangefs", "9p", "afs"}
_LOCAL_FS = {"tmpfs", "ramfs", "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs",
             "reiserfs", "jfs", "vfat", "devtmpfs", "overlay", "squashfs", "fuse.squashfuse"}


def _fs_kind_for_path(path) -> "tuple[str, str | None]":
    """``(kind, fstype)`` via /proc/self/mountinfo (longest mount-prefix match).
    kind ∈ {'shared','node_local','unknown'} — by actual mount fstype, not path
    name, so `/workspace`-is-local and other non-standard local mounts are caught."""
    fstype = None
    try:
        real = os.path.realpath(str(path))
        best = ""
        with open("/proc/self/mountinfo") as f:
            for line in f:
                try:
                    pre, post = line.split(" - ", 1)
                    mp = pre.split()[4]
                    ft = post.split()[0]
                except (ValueError, IndexError):
                    continue
                if (real == mp or real.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best):
                    best, fstype = mp, ft
    except Exception:  # noqa: BLE001
        return "unknown", None
    if fstype in _SHARED_FS:
        return "shared", fstype
    if fstype in _LOCAL_FS:
        return "node_local", fstype
    return "unknown", fstype


def _probe_envs_visible_on_compute_node(envs_dir: str, home: Path) -> "tuple[bool, str]":
    """Definitive shared-FS check: write a token under ENVS_DIR (the dir UNDER
    TEST) from the host running `aba doctor` (a submit node or interactive
    allocation), `sbatch` a one-shot job that reads it from a compute node and
    reports via stdout to a log under HOME (a known-shared channel — the env +
    launcher live there). Poll that shared log. Best-effort: `(True, …)` when
    sbatch is absent or the job doesn't land in time so a busy scheduler never
    blocks the install; a genuine MISSING is a hard `(False, …)`."""
    import time
    import uuid
    if not shutil.which("sbatch"):
        return True, "sbatch not found — compute-node probe skipped"
    envs = Path(envs_dir)
    try:
        envs.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return False, f"cannot create ENVS_DIR {envs_dir}: {e}"
    tok = uuid.uuid4().hex
    pdir = Path(home) / ".aba-probe"
    pdir.mkdir(parents=True, exist_ok=True)
    token = envs / f".aba-probe-{tok}"      # under the dir under test
    out_log = pdir / f"out-{tok}.log"        # shared channel back to the login node
    script = pdir / f"probe-{tok}.sh"
    token.write_text(tok)
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ -f '{token}' ] && [ \"$(cat '{token}' 2>/dev/null)\" = '{tok}' ]; then\n"
        "  echo ABA_PROBE_VISIBLE\nelse\n  echo ABA_PROBE_MISSING\nfi\n"
    )

    def _rm():
        for p in (token, script, out_log):
            try:
                p.unlink()
            except Exception:  # noqa: BLE001
                pass

    try:
        sub = subprocess.run(["sbatch", "--parsable", "-t", "5", "-n", "1",
                              "-o", str(out_log), str(script)],
                             capture_output=True, text=True, timeout=30)
        if sub.returncode != 0:
            _rm()
            return True, f"probe sbatch failed (skipped): {(sub.stderr or '').strip()[:160]}"
        deadline = time.time() + 180
        while time.time() < deadline:
            if out_log.exists():
                txt = out_log.read_text(errors="replace")
                if "ABA_PROBE_VISIBLE" in txt:
                    _rm()
                    return True, f"a compute node read a token under {envs_dir} (shared ✓)"
                if "ABA_PROBE_MISSING" in txt:
                    _rm()
                    return False, (f"a compute node could NOT read a token written under {envs_dir} — it "
                                   "is NOT shared; ensure_capability'd packages will be invisible to Slurm jobs")
            time.sleep(5)
        _rm()
        return True, "probe job didn't finish in 180s (inconclusive — scheduler busy?); fstype check stands"
    except Exception as e:  # noqa: BLE001
        _rm()
        return True, f"probe error (skipped): {e}"


def doctor() -> int:
    """Classical health-check of an existing install: env, frontend, recipes,
    launcher, credential, backend, and (on a cluster) Slurm. Prints a fix for
    each failure. The no-agent analog of the Mac repair flow."""
    _ensure_aba_home()
    from aba_installer import paths
    home = Path(paths.aba_home())
    fails = 0

    def chk(label: str, ok: bool, fix: str = "") -> None:
        nonlocal fails
        print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f"  → {fix}"), flush=True)
        if not ok:
            fails += 1

    print(f"ABA doctor — checking {home}\n", flush=True)
    chk("conda env (env/bin/uvicorn)", (home / "env" / "bin" / "uvicorn").exists(),
        "re-run the installer (create-env)")
    chk("frontend built (frontend/dist)",
        (home / "repo" / "aba" / "frontend" / "dist" / "index.html").exists(),
        "re-run build-frontend, or `aba update`")
    chk("recipe pack imported", (home / "installation" / "skills" / "recipes").exists(),
        "re-run import-recipes, or `aba update`")
    chk("launcher (bin/aba)", (home / "bin" / "aba").exists(), "re-run install-launcher")
    cfg = home / "config.env"
    # Credentials: config.env (apikey/oauth) OR the Claude-Code subscription OAuth that ABA
    # auto-uses (oauth_cc). A personal install commonly has NO cred in config.env and relies on
    # $CLAUDE_CODE_OAUTH_TOKEN / ~/.claude/.credentials.json — accept either (finding F7: the
    # config.env-only check false-failed on a working oauth_cc instance).
    cred_ok = (cfg.exists() and any(k in cfg.read_text() for k in
                                    ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ABA_LLM_CREDENTIAL"))) \
        or bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) \
        or (Path.home() / ".claude" / ".credentials.json").exists()
    chk("credential (config.env or Claude OAuth)", cred_ok,
        "set one: `aba auth --api-key sk-ant-…`, the OAuth flow, or a `claude` subscription login")
    # Backend health (best-effort; not a hard failure if the user hasn't started it).
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8000/api/health", timeout=3) as r:
            chk("backend responding (:8000)", r.status == 200, "start it: `aba up`")
    except Exception:  # noqa: BLE001
        print("  — backend not responding on :8000 (start it with `aba up` if expected)", flush=True)
    # Slurm — only relevant on a cluster-personal install.
    if shutil.which("sinfo"):
        try:
            up = subprocess.run(["sinfo", "-h", "-o", "%R"], capture_output=True, text=True, timeout=8)
            chk("Slurm reachable (sinfo)", up.returncode == 0,
                "check SLURM env / `module load slurm`")
            cfg_txt = cfg.read_text() if cfg.exists() else ""
            if "ABA_BATCH_SUBMITTER=slurm" not in cfg_txt:
                print("  — Slurm present but ABA_BATCH_SUBMITTER is not 'slurm' "
                      "(set it in config.env to offload jobs to the cluster)", flush=True)
        except Exception:  # noqa: BLE001
            chk("Slurm reachable (sinfo)", False, "check SLURM env / `module load slurm`")

    # Accelerator: if GPU compute exists (a gpu partition) or a CUDA base is declared,
    # the built base torch MUST be a CUDA build — else GPU jobs silently run on CPU on an
    # idle allocated GPU (the scVI-on-CPU incident; see docs/arch/envs.md).
    cfg_txt = cfg.read_text() if cfg.exists() else ""
    declared_cuda = "ABA_ACCELERATOR=cuda" in cfg_txt
    gpu_partition = False
    if shutil.which("sinfo"):
        try:
            g = subprocess.run(["sinfo", "-h", "-o", "%G"], capture_output=True, text=True, timeout=8)
            gpu_partition = "gpu" in (g.stdout or "").lower()
        except Exception:  # noqa: BLE001
            pass
    if declared_cuda or gpu_partition:
        envpy = home / "env" / "bin" / "python"
        cuda_build = None
        if envpy.exists():
            try:
                r = subprocess.run([str(envpy), "-c", "import torch; print(torch.version.cuda or '')"],
                                   capture_output=True, text=True, timeout=30)
                cuda_build = (r.stdout or "").strip() or None
            except Exception:  # noqa: BLE001
                pass
        chk(f"GPU torch build (declared={'cuda' if declared_cuda else 'auto'}, gpu partition="
            f"{'yes' if gpu_partition else 'no'})", cuda_build is not None,
            "GPU present but base torch is CPU-only — set ABA_ACCELERATOR=cuda in config.env "
            "and rebuild the env (GPU jobs would otherwise run on CPU)")

    # Provisioning dir must be SHARED across nodes when offloading to Slurm: a background
    # job runs on a different node than the submitter, so a package ensure_capability'd into
    # a node-local overlay (/tmp, /dev/shm, node-local scratch) is invisible to the job —
    # it dies on ModuleNotFoundError with no obvious cause (finding F6b, 2026-07).
    if "ABA_BATCH_SUBMITTER=slurm" in cfg_txt:
        import re as _re
        _me = _re.search(r"ABA_ENVS_DIR=(\S+)", cfg_txt)
        _mr = _re.search(r"ABA_RUNTIME_DIR=(\S+)", cfg_txt)
        if not _me and not _mr:
            # The default (config.py) is /workspace/aba-runtime — node-local scratch on
            # many clusters. Under Slurm that silently breaks provisioned-package jobs.
            chk("provisioning dir configured for shared storage (Slurm)", False,
                "neither ABA_RUNTIME_DIR nor ABA_ENVS_DIR is set in config.env — the default "
                "(/workspace/aba-runtime) is node-local on many clusters; set one to a shared path")
        else:
            _envs = (_me.group(1) if _me else _mr.group(1) + "/envs").strip("\"'")
            _kind, _fstype = _fs_kind_for_path(_envs)
            # Empirical fstype (not path prefix): catches /workspace, /local, bind-mounts.
            chk(f"provisioning dir on shared storage (Slurm; fs={_fstype or 'unknown'})",
                _kind != "node_local",
                f"ENVS_DIR is node-local ({_envs} on {_fstype}) — a background Slurm job on another node "
                "can't see ensure_capability'd packages; point ABA_RUNTIME_DIR/ABA_ENVS_DIR at shared storage")
            # Definitive: only probe when the fstype check didn't already fail (no point
            # sbatch-ing if we already know it's local). A genuine MISSING is a hard fail.
            if _kind != "node_local" and os.environ.get("ABA_SKIP_ENVS_PROBE", "").lower() not in ("1", "true", "yes"):
                _ok, _msg = _probe_envs_visible_on_compute_node(_envs, home)
                chk("provisioning dir visible from a compute node (Slurm probe)", _ok, _msg)

    # Settings registry surface (env_reorg): the declared config + any UNKNOWN ABA_*
    # env var present = a typo / stale knob. A drift here is worth surfacing even
    # when everything else is green.
    _n, _unknown = _settings_surface(home)
    if _n:
        print(f"  ✓ settings registry: {_n} declared "
              f"(inspect with `aba settings`)", flush=True)
        if _unknown:
            chk(f"no unrecognized ABA_* in the environment ({len(_unknown)} found)",
                False, f"unknown/typo'd: {', '.join(_unknown)} — "
                "check config.env; run `aba settings` for the full surface")

    print(f"\n{'✓ all checks passed' if fails == 0 else f'✗ {fails} issue(s) — see the fixes above'}", flush=True)
    return 0 if fails == 0 else 1


def _backend_python(home: Path) -> tuple[Path, Path] | tuple[None, None]:
    """(python, backend_dir) for the install, or (None, None) if not resolvable."""
    envpy = home / "env" / "bin" / "python"
    backend = home / "repo" / "aba" / "backend"
    if envpy.exists() and backend.exists():
        return envpy, backend
    return None, None


def _run_in_backend(home: Path, script: str, timeout: int = 30):
    """Run a snippet in the install's backend python (core.config importable)."""
    envpy, backend = _backend_python(home)
    if not envpy:
        return None
    env = dict(os.environ, PYTHONPATH=str(backend))
    try:
        return subprocess.run([str(envpy), "-c", script], capture_output=True,
                              text=True, timeout=timeout, env=env)
    except Exception:  # noqa: BLE001
        return None


def _settings_surface(home: Path) -> tuple[int, list]:
    """(#declared settings, [unknown ABA_* env vars]) via the backend registry.
    Returns (0, []) if the backend isn't resolvable (best-effort in doctor)."""
    r = _run_in_backend(home,
        "import json;from core.config import list_settings;"
        "d=list_settings();"
        "print(json.dumps({'n':len(d['settings']),'unknown':d['unknown_env']}))")
    if not r or r.returncode != 0 or not (r.stdout or "").strip():
        return 0, []
    try:
        import json
        d = json.loads(r.stdout.strip().splitlines()[-1])
        return int(d.get("n", 0)), list(d.get("unknown", []))
    except Exception:  # noqa: BLE001
        return 0, []


def settings_cmd(deploy_env: bool = False) -> int:
    """Print the full declared settings surface (value + source + tags), or, with
    --deploy-env, just the env keys the launcher must forward (the source of truth
    for the OOD forward-loop). Reads the install's backend registry."""
    _ensure_aba_home()
    from aba_installer import paths
    home = Path(paths.aba_home())
    if deploy_env:
        r = _run_in_backend(home,
            "from core.config import deploy_injected_keys;"
            "print('\\n'.join(deploy_injected_keys()))")
        if not r or r.returncode != 0:
            print("could not read the backend registry", flush=True)
            return 1
        print(r.stdout, end="", flush=True)
        return 0
    r = _run_in_backend(home,
        "from core.config import list_settings;"
        "d=list_settings();rows=d['settings'];"
        "w=max((len(x['name']) for x in rows), default=4);"
        "print(f\"{'name':<{w}}  {'value':<22} source        cat/weft/reduction\");"
        "[print(f\"{x['name']:<{w}}  {str(x['value'])[:22]:<22} {x['source']:<13} \"\n"
        "  f\"{x['category']}/{x['weft_fate']}/{x['reduction']}\") for x in rows];"
        "print();"
        "print('UNKNOWN ABA_* in env:', ', '.join(d['unknown_env']) or '(none)')")
    if not r or r.returncode != 0:
        print((r.stderr if r else "could not read the backend registry"), flush=True)
        return 1
    print(r.stdout, end="", flush=True)
    return 0


def _wall_to_h(val: str | None) -> int | None:
    """sacctmgr MaxWall ('D-HH:MM:SS' / 'HH:MM:SS') → whole hours. Blank / unlimited → None."""
    val = (val or "").strip()
    if not val or val.lower() in ("unlimited", "none", "-1"):
        return None
    days = 0
    if "-" in val:
        d, val = val.split("-", 1)
        days = int(d) if d.isdigit() else 0
    hh = val.split(":")[0]
    return days * 24 + (int(hh) if hh.isdigit() else 0)


def _discover_qos(user: str) -> tuple[list[str], dict, str | None]:
    """Probe `sacctmgr` for the user's valid QOS + each QOS's MaxWall + the user's
    account. Slurm exposes partitions via `sinfo` but NOT a user's QOS — without
    this, ABA submits no `--qos` and jobs land on the cluster default QOS (often an
    8h cap), so anything longer is rejected with QOSMaxWallDurationPerJobLimit.

    Returns (qos_ranked, {qos: max_h}, account); QOS ranked most-permissive first
    (largest MaxWall, ties toward generic/short names so a cross-partition `long`
    beats a partition-scoped `c_long`). Empty/None when sacctmgr is absent/quiet."""
    import getpass
    user = user or os.environ.get("USER") or getpass.getuser()
    if not shutil.which("sacctmgr"):
        return [], {}, None
    qos: set[str] = set()
    account: str | None = None
    try:
        r = subprocess.run(["sacctmgr", "-nP", "show", "assoc", f"user={user}",
                            "format=account,qos"], capture_output=True, text=True, timeout=20)
        for line in (r.stdout or "").splitlines():
            f = line.split("|")
            if len(f) >= 2:
                if f[0].strip() and account is None:
                    account = f[0].strip()
                qos.update(q.strip() for q in f[1].split(",") if q.strip())
    except Exception:  # noqa: BLE001
        return [], {}, None
    if not qos:
        return [], {}, account
    walls: dict[str, int | None] = {}
    try:
        r = subprocess.run(["sacctmgr", "-nP", "show", "qos", "format=name,maxwall"],
                           capture_output=True, text=True, timeout=20)
        for line in (r.stdout or "").splitlines():
            f = line.split("|")
            if len(f) >= 2 and f[0].strip():
                walls[f[0].strip()] = _wall_to_h(f[1])
    except Exception:  # noqa: BLE001
        pass
    _INF = 1 << 30
    ranked = sorted(qos, key=lambda q: (-(_INF if walls.get(q) is None else walls[q]), len(q), q))
    return ranked, {q: walls.get(q) for q in ranked}, account


def gen_hpc_config(out_path: str | None = None, *, write: bool = True) -> int:
    """Probe `sinfo` (+ `sacctmgr`) and write an `hpc.yaml` catalog. The RUNTIME now
    discovers partitions (sinfo) AND QOS + account (sacctmgr) live, so this file is
    a pure OPTIONAL OVERRIDE — generate it only to pin a partition list, reorder
    QOS, or force an account. `write=False` (the installer's default probe) prints
    what ABA detects without creating a file. Returns 0 on success, 1 when Slurm
    isn't reachable."""
    import re
    import yaml
    from aba_installer import paths
    out = Path(out_path or (Path(paths.aba_home()) / "hpc.yaml"))
    if not shutil.which("sinfo"):
        print("sinfo not found — skipping hpc.yaml (the router falls back to live "
              "queries or a hand-written config).")
        return 1
    res = subprocess.run(["sinfo", "-h", "-o", "%R|%c|%m|%l|%G"],
                         capture_output=True, text=True, timeout=10)
    parts: dict[str, dict] = {}
    for line in (res.stdout or "").splitlines():
        f = (line.split("|") + [""] * 5)[:5]
        name, cpn, mpn, tl, gres = (x.strip() for x in f)
        if not name:
            continue
        p = parts.setdefault(name, {"name": name, "max_cores": 0, "max_mem_gb": 0,
                                    "max_walltime_h": 24, "gpu": False})
        mc = re.match(r"(\d+)", cpn)             # %c is "22+" on heterogeneous partitions
        if mc:
            p["max_cores"] = max(p["max_cores"], int(mc.group(1)))
        m = re.match(r"(\d+)", mpn)
        if m:
            p["max_mem_gb"] = max(p["max_mem_gb"], round(int(m.group(1)) / 1024))
        d = re.match(r"(?:(\d+)-)?(\d+):", tl)               # D-HH:.. or HH:..
        if d:
            p["max_walltime_h"] = max(p["max_walltime_h"], int(d.group(1) or 0) * 24 + int(d.group(2)))
        if gres and "gpu" in gres.lower():
            p["gpu"] = True
    if not parts:
        print("sinfo returned no partitions — leaving hpc.yaml unwritten.")
        return 1
    for p in parts.values():                 # drop unparseable 0 ceilings → router uses its own default
        for k in ("max_cores", "max_mem_gb"):
            if not p.get(k):
                p.pop(k, None)
    qos_ranked, walls, account = _discover_qos(os.environ.get("USER", ""))
    primary_w = walls.get(qos_ranked[0]) if qos_ranked else None
    # Clamp partition walltime ceilings to the chosen QOS's MaxWall so the runtime
    # router never requests more time than the QOS allows (primary_w None =
    # unlimited QOS → keep the sinfo-derived ceiling).
    if primary_w:
        for p in parts.values():
            p["max_walltime_h"] = primary_w
    cfg: dict = {"hpc": {"partitions": list(parts.values()),
                         "qos": qos_ranked,
                         "defaults": {"partition": next(iter(parts)), "cores": 1,
                                      "mem_gb": 4, "walltime_h": 4}}}
    if account:
        cfg["hpc"]["account"] = account
    if write:
        out.write_text(yaml.safe_dump(cfg, sort_keys=False))
        print(f"wrote {out} with {len(parts)} partition(s): {', '.join(parts)}")
    else:
        print(f"detected {len(parts)} partition(s): {', '.join(parts)} "
              f"(not written — the runtime discovers partitions + QOS + account live)")
    if qos_ranked:
        pw = "unlimited" if primary_w is None else f"{primary_w}h"
        shown = ", ".join(f"{q}({'∞' if walls.get(q) is None else str(walls[q]) + 'h'})"
                          for q in qos_ranked[:8])
        print(f"  QOS: default '{qos_ranked[0]}' (MaxWall {pw}) of {len(qos_ranked)} available "
              f"[{shown}{'…' if len(qos_ranked) > 8 else ''}]"
              + (f"; account={account}" if account else ""))
        print("  (qos[0] is used for all jobs; reorder to prefer a higher-priority "
              "shorter-walltime QOS if your jobs are short)")
    else:
        print("  QOS: none discovered (sacctmgr absent/quiet) — jobs use the cluster default QOS.")
    return 0


def auth_cmd(api_key: str | None = None, token: str | None = None) -> int:
    """Set a credential headlessly: --api-key, --token (a `claude setup-token`
    value), or — with neither — the interactive paste-URL 'Sign in with Claude'
    flow (print a URL, the user approves on any browser and pastes the code)."""
    _ensure_aba_home()
    from aba_installer import auth
    if api_key:
        try:
            auth.persist_api_key(api_key)
            print("✓ API key saved to config.env")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"✗ {e}")
            return 1
    if token:
        try:
            auth.persist_setup_token(token)
            print("✓ Claude Code OAuth token saved to config.env")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"✗ {e}")
            return 1
    info = auth.build_headless_authorize_url()
    print("\nSign in with Claude (subscription) — headless:\n")
    print("  1. Open this URL in any browser (e.g. on your laptop):\n")
    print("     " + info["authorize_url"] + "\n")
    print("  2. Approve access. Claude shows a code — copy it.\n")
    try:
        pasted = input("  Paste the code (or the whole redirect URL): ").strip()
    except EOFError:
        print("✗ no input — run in an interactive terminal, or use "
              "`aba auth --token sk-ant-oat…` / `--api-key sk-ant-…`")
        return 1
    try:
        auth.complete_headless_oauth(pasted, state=info["state"],
                                     verifier=info["verifier"], redirect_uri=info["redirect_uri"])
    except Exception as e:  # noqa: BLE001
        print(f"\n✗ sign-in failed: {e}\n"
              "  Fallback: run `claude setup-token` locally, then "
              "`aba auth --token sk-ant-oat…`")
        return 1
    print("\n✓ signed in — subscription credentials written to config.env")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="aba-install", description="Headless ABA installer")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("install", help="run the install playbook")
    pi.add_argument("--headless", action="store_true", help="(implied; no UI)")
    pi.add_argument("--only", help="comma-separated step ids to run")
    pi.add_argument("--skip", help="comma-separated step ids to skip")
    sub.add_parser("update", help="pull latest code + recipes, refresh env, rebuild UI")
    sub.add_parser("doctor", help="diagnose an existing install")
    ps = sub.add_parser("settings", help="show the declared config settings surface (+ drift)")
    ps.add_argument("--deploy-env", action="store_true",
                    help="print only the env keys the launcher forwards (deploy_injected)")
    ph = sub.add_parser("hpc-config", help="write an optional hpc.yaml override (runtime discovers live)")
    ph.add_argument("--out", help="output path (default $ABA_HOME/hpc.yaml)")
    ph.add_argument("--print", dest="print_only", action="store_true",
                    help="probe + print what ABA detects; don't write a file")
    pa = sub.add_parser("auth", help="set a credential (OAuth sign-in / --token / --api-key)")
    pa.add_argument("--api-key", help="Anthropic API key (sk-ant-…)")
    pa.add_argument("--token", help="Claude Code OAuth token from `claude setup-token` (sk-ant-oat…)")
    a = p.parse_args(argv)
    if a.cmd == "hpc-config":
        return gen_hpc_config(a.out, write=not a.print_only)
    if a.cmd == "auth":
        return auth_cmd(a.api_key, a.token)
    if a.cmd == "install":
        only = a.only.split(",") if a.only else None
        skip = a.skip.split(",") if a.skip else None
        return run_playbook_headless("install", only=only, skip=skip)
    if a.cmd == "update":
        return run_playbook_headless("update")
    if a.cmd == "doctor":
        return doctor()
    if a.cmd == "settings":
        return settings_cmd(deploy_env=a.deploy_env)
    return 2


if __name__ == "__main__":
    sys.exit(main())
