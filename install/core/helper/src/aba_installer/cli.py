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

    print(f"\n{'✓ all checks passed' if fails == 0 else f'✗ {fails} issue(s) — see the fixes above'}", flush=True)
    return 0 if fails == 0 else 1


def gen_hpc_config(out_path: str | None = None) -> int:
    """Probe `sinfo` and write a starting hpc.yaml partition catalog (cluster-
    personal profile). The live router tolerates a stale/edited file, so this is
    a convenience: a good default the user refines (QOS/account/walltime). Returns
    0 on success, 1 when Slurm isn't reachable."""
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
        if cpn.isdigit():
            p["max_cores"] = max(p["max_cores"], int(cpn))
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
    cfg = {"hpc": {"partitions": list(parts.values()), "qos": [],
                   "defaults": {"partition": next(iter(parts)), "cores": 1,
                                "mem_gb": 4, "walltime_h": 4}}}
    out.write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"wrote {out} with {len(parts)} partition(s): {', '.join(parts)} "
          f"— edit it to set QOS / account / walltime caps.")
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
    ph = sub.add_parser("hpc-config", help="probe sinfo -> write a starting hpc.yaml")
    ph.add_argument("--out", help="output path (default $ABA_HOME/hpc.yaml)")
    a = p.parse_args(argv)
    if a.cmd == "hpc-config":
        return gen_hpc_config(a.out)
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
