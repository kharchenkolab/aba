"""Environment-integrity primitives (env_refactor.md P0).

Closes the gap that let corrupt installs be reported "ready": the old check was
``PathFinder.find_spec`` (does a spec EXIST), not a real import. A package
compiled against the wrong numpy ABI, a half-written package, or one missing a
system lib all HAVE a spec but **fail to load** — the tensorflow/scipy incidents
(2026-06-23/24). These helpers actually import the thing, on the real runtime
sys.path, in a throwaway subprocess.

Ground truth stays on the filesystem; these just probe it honestly. Language-
symmetric: ``verify_python_imports`` mirrors run_python's path assembly,
``verify_r_library`` wraps the existing ``r_has_package`` library()-load check.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


def verify_python_imports(
    import_names: Sequence[str],
    *,
    extra_paths: Optional[Sequence[str]] = None,
    python_exe: Optional[str] = None,
    timeout_s: int = 180,
) -> tuple[bool, str]:
    """Actually import each name in a fresh subprocess on the runtime sys.path
    (base interpreter + the pylib overlay **appended**, matching the run_python
    preamble). Returns ``(ok, detail)``.

    ``ok=False`` means present-but-unloadable — ABI mismatch, partial install,
    missing system lib — i.e. the exact "find_spec says yes, import explodes"
    case. ``detail`` carries the traceback tail for the agent/operator.

    ``extra_paths`` overrides the overlay paths (default: ``pylib_paths()``); use
    it to verify against a temp install prefix before merging it (transactional
    installs). ``python_exe`` defaults to the base interpreter.
    """
    names = [n for n in (import_names or []) if n]
    if not names:
        return True, ""
    exe = python_exe or sys.executable
    if extra_paths is None:
        # Mirror run_python's sys.path: shared overlay THEN the current project's
        # overlay (env_refactor.md P1), so verify sees exactly what a run_python
        # cell would import.
        from core.exec.materialize import pylib_paths, project_pylib_paths
        from core import projects
        extra_paths = ([str(p) for p in pylib_paths()]
                       + [str(p) for p in project_pylib_paths(projects.current())])
    # append (not prepend) so the base wins, exactly like the run_python preamble
    appends = "".join(f"sys.path.append({str(p)!r})\n" for p in (extra_paths or []))
    names_lit = ", ".join(repr(n) for n in names)
    script = (
        "import sys\n"
        f"{appends}"
        "import importlib\n"
        f"for _n in [{names_lit}]:\n"
        "    importlib.import_module(_n)\n"
        "print('ABA_IMPORT_OK')\n"
    )
    try:
        proc = subprocess.run(
            [exe, "-c", script], capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return False, f"import verification timed out after {timeout_s}s"
    except Exception as e:  # noqa: BLE001
        return False, f"could not launch import verification: {e}"
    if proc.returncode == 0 and "ABA_IMPORT_OK" in (proc.stdout or ""):
        return True, ""
    detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
    return False, detail[-1400:]


def base_constraints_path() -> Path:
    """Where the cached base-constraints (pip-freeze pin of the install-wide
    base) lives — next to the overlay it guards."""
    from core.exec.materialize import ENVS_DIR
    return Path(ENVS_DIR) / "base-constraints.txt"


_PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==")


def _freeze_pins(python_exe: Optional[str] = None) -> Optional[list[str]]:
    """`pip freeze` of an interpreter → clean ``name==version`` lines (drops
    editable/URL/VCS entries that are invalid as constraints). None on failure."""
    exe = python_exe or sys.executable
    try:
        proc = subprocess.run([exe, "-m", "pip", "freeze"],
                              capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    lines = [ln for ln in (proc.stdout or "").splitlines() if _PIN_RE.match(ln)]
    return lines or None


def canonical_lock_path() -> Optional[Path]:
    """A shipped/committed **canonical** base lock (the FULL intended scientific
    stack, pinned), if configured via ``$ABA_BASE_LOCK``. Preferred over the
    live-generated freeze so that on a MINIMAL install, on-demand installs still
    pin to the canonical versions (env_refactor.md P6, lazy-from-lock). None if
    not configured / missing."""
    import os
    p = os.environ.get("ABA_BASE_LOCK")
    return Path(p) if (p and Path(p).exists()) else None


def ensure_base_constraints(*, force: bool = False,
                            python_exe: Optional[str] = None) -> Optional[Path]:
    """The constraints file pinning the install-wide base so an overlay install
    can't move numpy / scipy or pull an incompatible-ABI wheel — it must satisfy
    the pin or fail loudly.

    Resolution order (P6): a shipped **canonical lock** (`$ABA_BASE_LOCK`) wins —
    so a minimal/lazy install pins to the full intended base; else the cached
    freeze; else generate one from the live base. Returns the path or ``None``
    (caller falls back to unconstrained + a logged warning — best-effort; never
    block a real install on lock generation). ``force=True`` re-locks from the
    live base (the "re-solve + re-lock" op)."""
    canon = canonical_lock_path()
    if canon is not None:
        return canon
    path = base_constraints_path()
    if path.exists() and not force:
        return path
    lines = _freeze_pins(python_exe)
    if not lines:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    return path


def write_base_lock(out_path, *, python_exe: Optional[str] = None) -> Optional[Path]:
    """Produce a **canonical** base lock by freezing the COMPLETE base — run on a
    fully-provisioned box, ship/commit the result, and point ``$ABA_BASE_LOCK``
    at it on minimal installs so they materialize the stack from the lock
    (env_refactor.md P6). Returns the path written, or None."""
    lines = _freeze_pins(python_exe)
    if not lines:
        return None
    out = Path(out_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    return out


def materialize_from_lock(packages: Sequence[str], *, prefix=None,
                          timeout_s: int = 1800) -> dict:
    """Install ``packages`` PINNED to the base lock — lazy base-fill / pre-warm
    for a minimal install (env_refactor.md P6). Defaults to the shared
    install-wide overlay; constrained so versions match the canonical lock.
    Returns {ok, installed, lock, error}."""
    lock = ensure_base_constraints()
    from core.exec.materialize import PYLIB_DIR
    target = Path(prefix) if prefix is not None else PYLIB_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    cmd = [sys.executable, "-m", "pip", "install", "--prefix", str(target)]
    if lock:
        cmd += ["-c", str(lock)]
    cmd += list(packages)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "installed": list(packages), "lock": str(lock) if lock else None,
                "error": f"materialize timed out after {timeout_s}s"}
    ok = proc.returncode == 0
    return {"ok": ok, "installed": list(packages), "lock": str(lock) if lock else None,
            "error": None if ok else (proc.stderr or proc.stdout or "")[-1200:]}


def _tier_of(location: Optional[str], project_id: Optional[str]) -> str:
    """Classify where a loaded module's file lives: base | shared-overlay |
    project-overlay | unknown — so the agent knows which tier owns it."""
    if not location:
        return "unknown"
    loc = str(location)
    from core.exec.materialize import pylib_paths, project_pylib_paths
    for p in project_pylib_paths(project_id):
        if loc.startswith(str(p)):
            return "project-overlay"
    for p in pylib_paths():
        if loc.startswith(str(p)):
            return "shared-overlay"
    return "base"


def python_package_status(name: str, *, project_id: Optional[str] = None,
                          extra_paths: Optional[Sequence[str]] = None,
                          timeout_s: int = 120) -> dict:
    """Diagnose one Python package on the runtime path (base + overlays):
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
    if extra_paths is None:
        from core.exec.materialize import pylib_paths, project_pylib_paths
        extra_paths = ([str(p) for p in pylib_paths()]
                       + [str(p) for p in project_pylib_paths(project_id)])
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
    try:
        proc = subprocess.run([sys.executable, "-c", script],
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
    out["tier"] = _tier_of(out.get("location"), project_id)
    return out


def env_overview(project_id: Optional[str] = None) -> dict:
    """A map of the Python tiers + their state — the no-package 'where am I'
    view: base interpreter, shared overlay, this project's overlay, and whether
    the base lock exists."""
    from core.exec.materialize import (PYLIB_DIR, pylib_paths,
                                       project_pylib_dir, project_pylib_paths)
    if project_id is None:
        from core import projects
        project_id = projects.current()

    def _populated(paths) -> bool:
        for p in paths:
            try:
                if Path(p).exists() and any(Path(p).iterdir()):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    return {
        "python": sys.executable,
        "shared_overlay": {"dir": str(PYLIB_DIR),
                           "populated": _populated(pylib_paths())},
        "project_overlay": {
            "project_id": project_id,
            "dir": str(project_pylib_dir(project_id)) if project_id else None,
            "populated": _populated(project_pylib_paths(project_id)),
        },
        "base_lock": {"path": str(base_constraints_path()),
                      "exists": base_constraints_path().exists()},
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


def _r_packages_by_lib(lib_paths: Sequence) -> dict:
    """One Rscript: installed.packages() grouped by LibPath, with the given libs
    prepended to .libPaths(). Returns {realpath(lib): [{name,version}]}."""
    import os
    paths = [str(p) for p in lib_paths if p]
    if not paths:
        return {}
    libs_r = "c(" + ", ".join(repr(p) for p in paths) + ")"
    expr = (f"libs <- {libs_r}; .libPaths(c(libs, .libPaths())); "
            f"ip <- installed.packages(); "
            f"if (nrow(ip)>0) for (i in seq_len(nrow(ip))) "
            f"cat('PKG\\t', ip[i,'LibPath'], '\\t', ip[i,'Package'], '\\t', ip[i,'Version'], '\\n', sep='')")
    try:
        from core.exec.r import _run_rscript
        proc = _run_rscript(expr, timeout_s=120)
    except Exception:  # noqa: BLE001
        return {}
    by: dict = {}
    for ln in (getattr(proc, "stdout", "") or "").splitlines():
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
    import sysconfig
    from core.exec import isolated_env as iso
    from core.exec.materialize import (PYLIB_DIR, pylib_paths, project_pylib_dir,
                                       project_pylib_paths, tools_env, _site_paths)
    if project_id is None:
        from core import projects
        project_id = projects.current()

    # ── Python ──
    base_site = sysconfig.get_path("purelib")
    py_layers = [
        {"tier": "base", "scope": "installation", "delivery": "baked", "mutable": False,
         "path": base_site, "packages": _py_packages([base_site])},
        {"tier": "shared overlay", "scope": "installation", "delivery": "on-demand", "mutable": True,
         "path": str(PYLIB_DIR), "packages": _py_packages([str(p) for p in pylib_paths()])},
    ]
    if project_id:
        py_layers.append(
            {"tier": "project overlay", "scope": "project", "project_id": project_id,
             "delivery": "on-demand", "mutable": True, "path": str(project_pylib_dir(project_id)),
             "packages": _py_packages([str(p) for p in project_pylib_paths(project_id)])})
    for name in iso.list_envs():
        py_layers.append(
            {"tier": "isolated", "scope": "capability", "delivery": "on-demand", "mutable": True,
             "name": name, "path": str(iso.env_dir(name)),
             "packages": _py_packages(_site_paths(iso.env_dir(name)))})
    lock = ensure_base_constraints()
    py = {"engine": "pip + venv", "layers": py_layers,
          "lock": {"path": str(lock) if lock else None,
                   "pins": len(lock.read_text().splitlines()) if lock and lock.exists() else 0,
                   "canonical": canonical_lock_path() is not None}}

    # ── R ──
    r_base_lib = tools_env() / "lib" / "R" / "library"
    r_proj_lib = None
    iso_r = []
    try:
        from core.exec.r import project_r_lib
        if project_id:
            r_proj_lib = project_r_lib(project_id)
    except Exception:  # noqa: BLE001
        pass
    iso_root = iso._isolated_root()
    if iso_root.exists():
        iso_r = sorted(p for p in iso_root.iterdir() if p.is_dir() and p.name.startswith("r-"))
    all_r_libs = [r_base_lib] + ([r_proj_lib] if r_proj_lib else []) + iso_r
    by = _r_packages_by_lib(all_r_libs)

    def _pkgs_for(lib):
        return by.get(os.path.realpath(str(lib)), []) if lib else []

    r_layers = [{"tier": "base", "scope": "installation", "delivery": "conda", "mutable": False,
                 "path": str(r_base_lib), "packages": _pkgs_for(r_base_lib)}]
    if r_proj_lib:
        r_layers.append({"tier": "project lib", "scope": "project", "project_id": project_id,
                         "delivery": "on-demand", "mutable": True, "path": str(r_proj_lib),
                         "packages": _pkgs_for(r_proj_lib)})
    for d in iso_r:
        r_layers.append({"tier": "isolated", "scope": "capability", "delivery": "on-demand",
                         "mutable": True, "name": d.name[2:], "path": str(d),
                         "packages": _pkgs_for(d)})
    r = {"engine": "install.packages + per-project libs + conda base", "layers": r_layers}

    return {"python": py, "r": r, "project_id": project_id}


def verify_r_library(libname: str, project_id: Optional[str] = None) -> tuple[bool, str]:
    """R analog of verify_python_imports: does ``library(libname)`` actually
    load (in the project's .libPaths + the shared base)? Wraps the existing
    ``r_has_package`` (Rscript + ``library()``), so R already has real
    load-verification — this just gives it the same (ok, detail) shape."""
    if not libname:
        return True, ""
    try:
        from core.exec.r import r_has_package
        ok = bool(r_has_package(libname, project_id=project_id))
        return ok, "" if ok else f"library({libname}) does not load on .libPaths()"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:400]
