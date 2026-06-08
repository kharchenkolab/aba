#!/usr/bin/env python3
"""Isolated, removable smoke test for the ABA macOS installer.

Runs the REAL install playbook (helper/src/aba_installer/playbook.yml) and
the same launcher-rendering step the helper uses — end to end — but
confined to a throwaway root so it is repeatable on any Mac and removable
with one command.

Everything the install touches is rooted under $ABA_SMOKE_ROOT (default
~/aba/.smoke) by redirecting $HOME: the conda env, the cloned repo,
~/bin/aba, ~/Library/... — all of it lands inside the throwaway tree, so
teardown is a single `rm -rf` and nothing on the real machine is mutated.

Source of truth is the *working tree*, not GitHub: ABA_ENV_YML_SRC and
ABA_REPO_SRC point the playbook at this checkout, so un-pushed changes are
what get installed and tested.

Layout under the root:
  <root>/home/                      → the redirected $HOME for the install
  <root>/home/.aba/                 → ABA_HOME (env, repo, launcher, …)
  <root>/mamba/                     → conda package cache (kept across runs
                                      unless --purge, so re-installs are fast)
  <root>/bin/micromamba             → the micromamba binary (kept)
  <root>/empty-recipes/             → stand-in for aba-recipes

Usage:
  smoke.py up                 full from-scratch install (all steps)
  smoke.py run STEP [STEP…]   run specific playbook steps (see --list)
  smoke.py --list             list step ids
  smoke.py serve              start the backend + verify it serves the UI
  smoke.py stop               stop the backend
  smoke.py status             what's installed / running
  smoke.py down [--purge]     remove the install (--purge also drops caches)

The slow step is `create-env` (~700 MB download, several minutes); run it
on its own (`run create-env`) in the background when iterating.
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# install/mac/devtest/smoke.py → repo root is parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER_SRC = REPO_ROOT / "install" / "mac" / "helper" / "src"
ENV_YML = REPO_ROOT / "install" / "mac" / "environment.yml"
PLAYBOOK = HELPER_SRC / "aba_installer" / "playbook.yml"

ROOT = Path(os.environ.get("ABA_SMOKE_ROOT", Path.home() / "aba" / ".smoke"))
FAKE_HOME = ROOT / "home"
ABA_HOME = FAKE_HOME / ".aba"
MAMBA_PKGS = ROOT / "mamba"
EMPTY_RECIPES = ROOT / "empty-recipes"
SEED_MICROMAMBA = ROOT / "bin" / "micromamba"
BACKEND_PORT = 8000


def _isolated_env() -> dict[str, str]:
    """The environment that confines the whole install under ROOT."""
    env = dict(os.environ)
    env.update(
        HOME=str(FAKE_HOME),
        ABA_HOME=str(ABA_HOME),
        MAMBA_ROOT_PREFIX=str(MAMBA_PKGS),
        ABA_ENV_YML_SRC=str(ENV_YML),
        ABA_REPO_SRC=str(REPO_ROOT),
        ABA_RECIPES_SRC=str(EMPTY_RECIPES),
    )
    # Keep micromamba's own config/cache out of the real $HOME too.
    env.pop("CONDARC", None)
    return env


def _setup_dirs(env: dict[str, str]) -> None:
    for d in (FAKE_HOME, ABA_HOME / "bin", MAMBA_PKGS, EMPTY_RECIPES,
              FAKE_HOME / "bin", FAKE_HOME / "Library" / "LaunchAgents"):
        d.mkdir(parents=True, exist_ok=True)
    # Pre-seed micromamba so `create-env` can run standalone without first
    # running `install-micromamba`. The install-micromamba step still
    # re-downloads (overwrites) when run, exercising the real path.
    target = ABA_HOME / "bin" / "micromamba"
    if not target.exists() and SEED_MICROMAMBA.exists():
        shutil.copy2(SEED_MICROMAMBA, target)
        target.chmod(0o755)


def _import_helper():
    if str(HELPER_SRC) not in sys.path:
        sys.path.insert(0, str(HELPER_SRC))
    from aba_installer import control, playbook  # noqa: E402
    return control, playbook


def _on_event(name: str, payload: dict) -> None:
    if name == "step_start":
        print(f"\n\033[1m▸ {payload['title']}\033[0m  ({payload['step_id']})")
    elif name == "command_start":
        cmd = payload["command"]
        print(f"    $ {cmd[:140]}{'…' if len(cmd) > 140 else ''}")
    elif name == "command_end":
        mark = "✓" if payload["ok"] else "✗"
        print(f"    {mark} exit={payload['exit_code']} ({payload['duration_s']:.1f}s)")
    elif name == "step_end":
        if not payload["ok"]:
            print(f"  \033[31mstep failed: {payload['error']}\033[0m")


def _run_steps(only: list[str] | None) -> int:
    env = _isolated_env()
    _setup_dirs(env)
    # Mirror the Python-side env vars so the helper's aba_home()/launcher
    # render targets the isolated tree (control.prepare_install_artifacts
    # reads os.environ).
    os.environ.update({k: env[k] for k in
                       ("HOME", "ABA_HOME", "MAMBA_ROOT_PREFIX",
                        "ABA_ENV_YML_SRC", "ABA_REPO_SRC", "ABA_RECIPES_SRC")})
    control, playbook = _import_helper()

    # Render the launcher (gap fix #3) before install-launcher runs.
    launcher = control.prepare_install_artifacts()
    print(f"rendered launcher → {launcher}")

    pb = playbook.load_playbook(PLAYBOOK)
    ex = playbook.Executor(pb, on_event=_on_event, base_env=env)
    results = ex.run_all(only=set(only) if only else None)

    failed = [r for r in results if not r.ok]
    for r in failed:
        bad = next((c for c in r.commands if not c.ok), None)
        if bad:
            print(f"\n\033[31m── {r.step_id} failed ──\033[0m\n$ {bad.command}")
            if bad.stdout.strip():
                print("stdout:\n" + bad.stdout[-4000:])
            if bad.stderr.strip():
                print("stderr:\n" + bad.stderr[-4000:])
    ok = bool(results) and all(r.ok for r in results)
    print(f"\n{'✓ all steps OK' if ok else '✗ install incomplete'} "
          f"({len(results)} step(s) run)")
    return 0 if ok else 1


def _launcher_path() -> Path:
    return FAKE_HOME / "bin" / "aba"


def cmd_serve() -> int:
    env = _isolated_env()
    aba = _launcher_path()
    if not aba.exists():
        print(f"launcher not installed yet ({aba}); run `up` first")
        return 1
    print("starting backend via `aba up` …")
    p = subprocess.run([str(aba), "up"], env=env, capture_output=True, text=True)
    print(p.stdout.strip() or p.stderr.strip())
    if p.returncode != 0:
        return 1
    # Poll the health endpoint, then the SPA shell.
    base = f"http://127.0.0.1:{BACKEND_PORT}"
    ok = False
    for _ in range(60):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                if r.status == 200:
                    ok = True
                    break
        except Exception:
            time.sleep(1)
    if not ok:
        print("backend did not come up; tail the log:")
        _tail_log(env)
        return 1
    print(f"✓ /api/health 200 at {base}")
    # SPA shell
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            body = r.read(2000).decode("utf-8", "replace")
        is_html = "<!doctype html" in body.lower() or "<html" in body.lower()
        print(f"{'✓' if is_html else '✗'} GET / returns "
              f"{'the SPA shell (HTML)' if is_html else 'non-HTML: ' + body[:200]}")
    except Exception as e:
        print(f"✗ GET / failed: {e}")
    print(f"\nLeft running at {base} — `smoke.py stop` to stop, "
          f"`smoke.py serve` re-checks.")
    return 0


def _tail_log(env: dict[str, str]) -> None:
    log = ABA_HOME / "logs" / "backend.log"
    if log.exists():
        print(log.read_text(errors="replace")[-3000:])
    else:
        print(f"(no log at {log})")


def cmd_stop() -> int:
    env = _isolated_env()
    aba = _launcher_path()
    if aba.exists():
        p = subprocess.run([str(aba), "stop"], env=env, capture_output=True, text=True)
        print(p.stdout.strip() or p.stderr.strip() or "stopped")
    else:
        subprocess.run(["pkill", "-f", "[u]vicorn main:app"], check=False)
        print("stopped (pkill fallback)")
    return 0


def cmd_status() -> int:
    env_dir = ABA_HOME / "env"
    repo = ABA_HOME / "repo" / "aba"
    dist = repo / "frontend" / "dist" / "index.html"
    aba = _launcher_path()
    running = subprocess.run(["pgrep", "-f", "uvicorn.*main:app"],
                             capture_output=True, text=True).stdout.strip()
    print(f"root:            {ROOT}")
    print(f"  micromamba:    {'✓' if SEED_MICROMAMBA.exists() else '—'} {SEED_MICROMAMBA}")
    print(f"  conda env:     {'✓' if env_dir.exists() else '—'} {env_dir}")
    print(f"  repo (aba):    {'✓' if repo.exists() else '—'} {repo}")
    print(f"  frontend dist: {'✓' if dist.exists() else '—'} {dist}")
    print(f"  launcher:      {'✓' if aba.exists() else '—'} {aba}")
    print(f"  backend:       {'running pid ' + running if running else 'not running'}")
    if ROOT.exists():
        du = subprocess.run(["du", "-sh", str(ROOT)], capture_output=True, text=True).stdout.split()
        print(f"  disk:          {du[0] if du else '?'}")
    return 0


def cmd_down(purge: bool) -> int:
    cmd_stop()
    if purge:
        if ROOT.exists():
            shutil.rmtree(ROOT, ignore_errors=True)
        print(f"purged {ROOT}")
    else:
        # Keep the package cache + micromamba binary for fast re-installs.
        if FAKE_HOME.exists():
            shutil.rmtree(FAKE_HOME, ignore_errors=True)
        print(f"removed {FAKE_HOME} (kept {MAMBA_PKGS} cache + micromamba; "
              f"--purge to drop those too)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="status",
                    choices=["up", "run", "serve", "stop", "status", "down"])
    ap.add_argument("steps", nargs="*", help="step ids for `run`")
    ap.add_argument("--purge", action="store_true", help="`down`: also drop caches")
    ap.add_argument("--list", action="store_true", help="list playbook step ids")
    args = ap.parse_args()

    if args.list:
        _, playbook = _import_helper()
        pb = playbook.load_playbook(PLAYBOOK)
        for s in pb.steps:
            print(f"  {s.id:20s} {s.title}")
        return 0

    if args.command == "up":
        return _run_steps(None)
    if args.command == "run":
        if not args.steps:
            print("run: give one or more step ids (see --list)")
            return 2
        return _run_steps(args.steps)
    if args.command == "serve":
        return cmd_serve()
    if args.command == "stop":
        return cmd_stop()
    if args.command == "down":
        return cmd_down(args.purge)
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
