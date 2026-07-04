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


def run_playbook_headless(name: str, *, only=None, skip=None) -> int:
    """Load + run a playbook synchronously, streaming to stdout. Returns 0 on
    full success, 1 if a step failed (it stops there, remediation printed)."""
    _ensure_aba_home()
    from aba_installer import control
    if name == "install":
        control.prepare_install_artifacts()           # render the aba launcher template etc.
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
    cred_ok = cfg.exists() and any(k in cfg.read_text() for k in
                                   ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ABA_LLM_CREDENTIAL"))
    chk("credential (config.env)", cred_ok,
        "set one: `aba auth --api-key sk-ant-…` or the OAuth flow")
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

    print(f"\n{'✓ all checks passed' if fails == 0 else f'✗ {fails} issue(s) — see the fixes above'}", flush=True)
    return 0 if fails == 0 else 1


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
    return 2


if __name__ == "__main__":
    sys.exit(main())
