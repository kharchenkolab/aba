"""Isolated environments (env_refactor.md P4) — the escape hatch + the agent's
troubleshooting sandbox.

When a requirement is UNSAT against the install-wide base (a conflicting numpy,
tensorflow, an ABI-incompatible wheel) — or the agent simply needs to resolve a
conflict its own way — it gets a FULL, independent environment it OWNS. Isolation
contains any mess to that env; the shared base is never touched. This is the
"graduated control by blast radius" tier: full control over what the agent
creates, guarded requests against shared state.

Engine: ``uv`` when available (hardlink cache → cheap create/install), else
stdlib ``venv`` + the env's own pip. The surface is engine-agnostic, so adopting
uv later is a pure speedup with no API change.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


# env_refactor.md §11.2 — reserved env names. These denote the project's normal
# served stack (`env="default"`) or the layer vocabulary; an isolated env may not
# take one, so `run_python(env=…)` can unambiguously tell "the default stack" from
# "a named isolated env".
RESERVED_ENV_NAMES = frozenset({"default", "base", "shared", "project"})


def is_reserved_name(name: str) -> bool:
    return (name or "").strip().lower() in RESERVED_ENV_NAMES


def _check_name(name: str) -> None:
    if is_reserved_name(name):
        raise ValueError(
            f"'{name}' is a reserved env name (default/base/shared/project) — it "
            "denotes the normal served stack, not an isolated env. Pick another name.")


def _isolated_root() -> Path:
    from core.exec.materialize import ENVS_DIR
    return Path(ENVS_DIR) / "isolated"


def _proj_root(project_id=None) -> Path:
    """A project's own isolated envs (§11.6 project-scoped — the default)."""
    from core import projects
    pid = project_id or projects.current() or "_none"
    return _isolated_root() / "proj" / str(pid)


def _shared_root() -> Path:
    """Restricted install-wide envs, shared across projects (resolution fallback)."""
    return _isolated_root() / "shared"


def _resolve_root(name: str, project_id=None) -> Path:
    """The dir CONTAINING this env — the project's own env if present, else a shared
    install-wide env if THAT is present, else the project root (creation target).
    So a project NEVER collides with another project's same-named env."""
    proot = _proj_root(project_id)
    if (proot / name).exists() or (proot / f"r-{name}").exists():
        return proot
    sroot = _shared_root()
    if (sroot / name).exists() or (sroot / f"r-{name}").exists():
        return sroot
    return proot


def env_dir(name: str, project_id=None) -> Path:
    return _resolve_root(name, project_id) / str(name)


def env_python(name: str, project_id=None) -> Path:
    return env_dir(name, project_id) / "bin" / "python"


def uv_path() -> Optional[str]:
    """uv on PATH or beside the base interpreter — None if not installed (then we
    fall back to stdlib venv)."""
    found = shutil.which("uv")
    if found:
        return found
    cand = Path(sys.executable).parent / "uv"
    return str(cand) if cand.exists() else None


def engine() -> str:
    return "uv" if uv_path() else "venv"


def create_env(name: str, *, with_base_site_packages: bool = False,
               timeout_s: int = 300) -> dict:
    """Create (idempotently) an isolated env the agent owns. uv if available
    (fast), else stdlib venv. ``with_base_site_packages`` shares the base's
    packages (use only for the *additive* case — a conflict needs its own copy,
    so leave it False). Returns {name, python, engine, created}."""
    _check_name(name)
    d = _proj_root() / str(name)          # always the PROJECT's own env (never shared)
    py = d / "bin" / "python"
    if py.exists():
        return {"name": name, "python": str(py), "created": False, "engine": "existing"}
    d.parent.mkdir(parents=True, exist_ok=True)
    uv = uv_path()
    if uv:
        cmd = [uv, "venv", str(d), "--python", sys.executable]
        if with_base_site_packages:
            cmd.append("--system-site-packages")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        if proc.returncode != 0:
            raise RuntimeError(f"uv venv failed: {(proc.stderr or proc.stdout)[-600:]}")
        eng = "uv"
    else:
        import venv as _venv
        _venv.create(d, with_pip=True, system_site_packages=with_base_site_packages)
        eng = "venv"
    return {"name": name, "python": str(env_python(name)), "created": True, "engine": eng}


def install_into(name: str, specs: Sequence[str], *, timeout_s: int = 1200,
                 verify_imports: Optional[Sequence[str]] = None) -> dict:
    """Install specs into the isolated env with FULL resolution control (any
    versions, conflicting with the base — that's the point). uv pip if uv, else
    the env's pip. Optionally import-verify afterwards (the same honesty as the
    overlay path). Returns {name, ok, installed, error, verified}."""
    if not env_python(name).exists():
        create_env(name)
    py = env_python(name)
    uv = uv_path()
    if uv:
        cmd = [uv, "pip", "install", "--python", str(py), *specs]
    else:
        cmd = [str(py), "-m", "pip", "install", *specs]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "installed": list(specs),
                "error": f"install timed out after {timeout_s}s", "verified": None}
    ok = proc.returncode == 0
    out: dict = {"name": name, "ok": ok, "installed": list(specs),
                 "error": None if ok else (proc.stderr or proc.stdout or "")[-1400:],
                 "verified": None}
    if ok and verify_imports:
        v = run_in(name, "import " + ", ".join(verify_imports) + "\nprint('ABA_VERIFY_OK')")
        out["verified"] = ("ABA_VERIFY_OK" in (v.get("stdout") or ""))
        if not out["verified"]:
            out["ok"] = False
            out["error"] = "installed but import-verify failed:\n" + (v.get("stderr") or "")[-800:]
    return out


def run_in(name: str, code: str, *, timeout_s: int = 600) -> dict:
    """Run Python code inside the isolated env. Returns {ok, stdout, stderr}."""
    py = env_python(name)
    if not py.exists():
        return {"ok": False, "stdout": "", "stderr": f"isolated env {name!r} does not exist"}
    try:
        proc = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"run timed out after {timeout_s}s"}
    return {"ok": proc.returncode == 0, "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-2000:]}


def list_envs(project_id=None) -> list[str]:
    """The current project's own python envs + the shared install-wide ones."""
    out = set()
    for root in (_proj_root(project_id), _shared_root()):
        if root.exists():
            for p in root.iterdir():
                if (p / "bin" / "python").exists():
                    out.add(p.name)
    return sorted(out)


def remove_env(name: str, project_id=None) -> bool:
    # Only removes the PROJECT's own env (a project can't delete a shared one).
    proot = _proj_root(project_id)
    d = proot / name
    existed = d.exists()
    if existed:
        shutil.rmtree(d, ignore_errors=True)
    (proot / "_specs" / f"{name}.json").unlink(missing_ok=True)   # §11.6: drop spec too
    (proot / "_specs" / f"{name}.used").unlink(missing_ok=True)
    return existed


# ── active env pointer (§11.2) — per-project, per-language. Bare run_python /
#    run_r (env=None) follow it; it defaults to "default" (the served stack). ──
def _active_env_file(project_id: str) -> Path:
    from core.config import PROJECTS_DIR
    return Path(PROJECTS_DIR) / str(project_id) / "active_envs.json"


def get_active_env(project_id, lang: str = "python") -> str:
    if not project_id:
        return "default"
    f = _active_env_file(project_id)
    if not f.exists():
        return "default"
    try:
        import json
        return (json.loads(f.read_text()) or {}).get(lang) or "default"
    except Exception:  # noqa: BLE001
        return "default"


def set_active_env(project_id: str, name: str, lang: str = "python") -> dict:
    import json
    f = _active_env_file(project_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text()) or {}
        except Exception:  # noqa: BLE001
            data = {}
    data[lang] = name
    f.write_text(json.dumps(data))
    return {"lang": lang, "active": name}


# ── per-env spec + lock (§11.6) — persists OUTSIDE the built env dir so it
#    survives GC; lets a reclaimed env rebuild reproducibly on next use. ──
def env_spec_path(name: str, project_id=None) -> Path:
    return _resolve_root(name, project_id) / "_specs" / f"{name}.json"


def capture_env_spec(name: str, *, language: str = "python", packages=None) -> dict:
    """Snapshot an env to a persistent spec: the requested packages + (Python) a
    pip-freeze lock for pinned, reproducible rebuild."""
    import json
    spec = {"name": name, "language": language, "packages": list(packages or [])}
    if language == "python":
        py = env_python(name)
        if py.exists():
            fr = subprocess.run([str(py), "-m", "pip", "freeze", "--local"],
                                capture_output=True, text=True, timeout=120)
            if fr.returncode == 0:
                spec["lock"] = [ln.strip() for ln in fr.stdout.splitlines()
                                if "==" in ln and not ln.startswith("-e")]
    p = env_spec_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(spec))
    return spec


def load_env_spec(name: str):
    import json
    p = env_spec_path(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def ensure_env_built(name: str, *, timeout_s: int = 1800) -> bool:
    """True iff the built env exists — rebuilding it from its lock if a GC reclaimed
    the bytes (§11.6 lazy rebuild). Fast no-op when the env is already on disk.
    Touches the use marker so GC sees the env as recently used."""
    if env_python(name).exists():
        touch_env(name)
        return True
    spec = load_env_spec(name)
    if not spec or spec.get("language") != "python":
        return False
    create_env(name)
    pkgs = spec.get("lock") or spec.get("packages") or []
    if pkgs:
        subprocess.run([str(env_python(name)), "-m", "pip", "install", "-q", *pkgs],
                       capture_output=True, text=True, timeout=timeout_s)
    if env_python(name).exists():
        touch_env(name)
        return True
    return False


# ── lazy GC (§11.6): reclaim the built bytes of idle, rebuildable envs; the spec
#    stays so the next use rebuilds from the lock transparently. ──
def env_used_marker(name: str, project_id=None) -> Path:
    return _resolve_root(name, project_id) / "_specs" / f"{name}.used"


def touch_env(name: str) -> None:
    m = env_used_marker(name)
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text("")            # mtime = now


def env_idle_seconds(name: str):
    import time
    m = env_used_marker(name)
    ref = m if m.exists() else (env_dir(name) if env_dir(name).exists() else None)
    if ref is None:
        return None
    try:
        return time.time() - ref.stat().st_mtime
    except Exception:  # noqa: BLE001
        return None


def gc_isolated_envs(*, max_idle_s: int = 30 * 86400, dry_run: bool = False) -> list[str]:
    """Reclaim the built bytes of python envs idle past ``max_idle_s`` that HAVE a
    spec (so they rebuild on next use), across ALL project roots + the shared root.
    The spec + use-marker are kept; only the heavy venv dir is removed."""
    import time
    reclaimed: list[str] = []
    isr = _isolated_root()
    roots: list[Path] = []
    if (isr / "proj").exists():
        roots += [d for d in (isr / "proj").iterdir() if d.is_dir()]
    if (isr / "shared").exists():
        roots.append(isr / "shared")
    for root in roots:
        for p in root.iterdir():
            if not (p / "bin" / "python").exists():
                continue
            if not (root / "_specs" / f"{p.name}.json").exists():   # not rebuildable → keep
                continue
            used = root / "_specs" / f"{p.name}.used"
            ref = used if used.exists() else p
            try:
                idle = time.time() - ref.stat().st_mtime
            except Exception:  # noqa: BLE001
                continue
            if idle <= max_idle_s:
                continue
            if not dry_run:
                shutil.rmtree(p, ignore_errors=True)
            reclaimed.append(p.name)
    return reclaimed


# ── R isolated environments (env_refactor.md P3) ─────────────────────────────
# R has no separate interpreter to isolate (one R, many library dirs), so an
# "isolated R env" is a standalone library dir installed into + run with that
# dir FIRST on .libPaths(). NOTE: R's *per-project* lib is already prepended to
# .libPaths() (libpaths_expr → project wins over base), so a project ALREADY
# overrides base package versions — the case-(i) escape hatch Python needed a
# venv for. These isolated R libs are for case-(ii)/symmetry: a fully separate,
# project-independent lib for a one-off conflicting install.

def r_env_lib(name: str, project_id=None) -> Path:
    return _resolve_root(name, project_id) / f"r-{name}"


def r_create_env(name: str) -> dict:
    _check_name(name)
    lib = _proj_root() / f"r-{name}"      # always the PROJECT's own R lib
    created = not lib.exists()
    lib.mkdir(parents=True, exist_ok=True)
    return {"name": name, "lib": str(lib), "created": created,
            "engine": "r-libdir", "language": "r"}


def r_run_in(name: str, code: str, *, timeout_s: int = 600) -> dict:
    """Run R code with the isolated lib FIRST on .libPaths() (so its packages
    win), then the shared base. Returns {ok, stdout, stderr}."""
    lib = r_env_lib(name)
    if not lib.exists():
        return {"ok": False, "stdout": "", "stderr": f"isolated R env {name!r} does not exist"}
    from core.exec.r import _run_rscript
    expr = f".libPaths(c({str(lib)!r}, .libPaths()))\n{code}"
    proc = _run_rscript(expr, timeout_s)
    return {"ok": proc.returncode == 0,
            "stdout": (getattr(proc, "stdout", "") or "")[-4000:],
            "stderr": (getattr(proc, "stderr", "") or "")[-2000:]}


def r_install_into(name: str, packages: Sequence[str], *, timeout_s: int = 1800,
                   verify: bool = True) -> dict:
    """Install R packages into the isolated lib (binary via PPM where available).
    The isolated lib is the install target AND first on .libPaths(), so shared
    deps resolve from the base but the named packages live isolated. Returns
    {name, ok, installed, error, verified}."""
    lib = r_env_lib(name)
    lib.mkdir(parents=True, exist_ok=True)
    from core.exec.r import _run_rscript, cran_repo
    pkgs = "c(" + ", ".join(repr(p) for p in packages) + ")"
    expr = (f".libPaths(c({str(lib)!r}, .libPaths()))\n"
            f"install.packages({pkgs}, lib={str(lib)!r}, repos={cran_repo()!r})")
    proc = _run_rscript(expr, timeout_s)
    ok = proc.returncode == 0
    out: dict = {"name": name, "ok": ok, "installed": list(packages),
                 "error": None if ok else
                 ((getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "")[-1400:]),
                 "verified": None}
    if ok and verify:
        v = r_run_in(name, f"ok <- all(vapply({pkgs}, requireNamespace, logical(1), "
                           f"quietly=TRUE)); cat(if (ok) 'ABA_VERIFY_OK' else 'ABA_VERIFY_FAIL')")
        out["verified"] = "ABA_VERIFY_OK" in (v.get("stdout") or "")
        if not out["verified"]:
            out["ok"] = False
            out["error"] = "installed but library() verification failed:\n" + (v.get("stderr") or "")[-600:]
    return out


def remove_r_env(name: str) -> bool:
    lib = r_env_lib(name)
    if lib.exists():
        shutil.rmtree(lib, ignore_errors=True)
        return True
    return False
