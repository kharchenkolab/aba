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

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence


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


# §11.4 — the ABI anchor: the cross-cutting compiled-stack packages a project
# overlay must NOT override (numpy's 1.x↔2.x ABI break is the one that bit us).
# Pinning JUST these (not the full base freeze) lets a project override ordinary
# package versions while the compiled foundation stays coherent.
_ABI_ANCHOR = ("numpy",)


def abi_anchor_path() -> Path:
    from core.exec.materialize import ENVS_DIR
    return Path(ENVS_DIR) / "abi-anchor.txt"


def _anchor_pins_from_metadata(python_exe: Optional[str] = None) -> list[str]:
    """Pin the ABI-anchor packages to their INSTALLED versions, read from package
    metadata. Robust to how the package was delivered: a conda-forge / local-wheel
    install renders in `pip freeze` as ``numpy @ file:///…`` (NOT ``numpy==2.4.6``),
    which _freeze_pins drops as an invalid constraint — so on a conda scientific base
    the freeze carries no numpy and the anchor would be empty. Metadata knows the
    version either way. In-process (python_exe=None) reads THIS interpreter (== the
    base that overlay installs target, since materialize pip-installs with
    sys.executable); a python_exe reads that interpreter's metadata via subprocess."""
    if python_exe and python_exe != sys.executable:
        try:
            code = ("import importlib.metadata as m,json;"
                    "print(json.dumps({n:(m.version(n)) for n in %r}))" % (list(_ABI_ANCHOR),))
            proc = subprocess.run([python_exe, "-c", code], capture_output=True,
                                  text=True, timeout=30)
            vers = json.loads(proc.stdout) if proc.returncode == 0 else {}
        except Exception:  # noqa: BLE001
            vers = {}
        return [f"{n}=={v}" for n, v in vers.items() if v]
    import importlib.metadata as _md
    pins = []
    for name in _ABI_ANCHOR:
        try:
            pins.append(f"{name}=={_md.version(name)}")
        except Exception:  # noqa: BLE001 — anchor pkg not importable here; skip it
            pass
    return pins


def _file_has_anchor_pin(path: Path) -> bool:
    """True iff the cached anchor file actually pins an anchor package (guards against
    a STALE/empty cache written before the anchor could be resolved)."""
    try:
        lines = path.read_text().splitlines()
    except Exception:  # noqa: BLE001
        return False
    return any("==" in ln and ln.split("==")[0].strip().lower() in _ABI_ANCHOR
               for ln in lines)


def abi_anchor_constraints(*, force: bool = False,
                           python_exe: Optional[str] = None) -> Optional[Path]:
    """Small constraint pinning only the ABI-anchor packages (numpy) to their base
    versions — used for project-overlay installs so an override can't shadow-break
    the compiled stack (§11.4). Reads the version from live package METADATA (robust
    to conda/local-wheel installs), falling back to the base-freeze extraction; None
    only if the anchor truly can't be resolved. Revalidates a cached file so a stale
    empty anchor is regenerated."""
    out = abi_anchor_path()
    if out.exists() and not force and _file_has_anchor_pin(out):
        return out
    pins = _anchor_pins_from_metadata(python_exe)
    if not pins:                                   # legacy ==-pinned deployments
        base = ensure_base_constraints(python_exe=python_exe)
        pins = [ln.strip() for ln in (base.read_text().splitlines() if base and base.exists() else [])
                if "==" in ln and ln.split("==")[0].strip().lower() in _ABI_ANCHOR]
    if not pins:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(pins) + "\n")
    return out


def env_selfcheck(*, python_exe: Optional[str] = None) -> dict:
    """Fast, structured check of the env-layering invariants a run should hold before
    it trusts the stack — and it ARMS the ABI anchor as a side effect (idempotent).

    Complements self_heal_base(): that verifies the base dependency CLOSURE (pip check
    + deep import) but NOT the guard config. This catches the SILENT failure that the
    deep check misses — the ABI-anchor (numpy pin) being unresolved, which on a conda
    scientific base (pip freeze renders numpy as ``@ file://`` → dropped by the
    freeze-based anchor) leaves overlay installs UNCONSTRAINED and lets pip rebuild
    numpy (the GCC-too-old provisioning failures). Returns {ok, checks:{name:{ok,detail}}}."""
    checks: dict = {}
    anchor = abi_anchor_constraints(python_exe=python_exe)   # resolves + writes (arms) the pin
    armed = bool(anchor and _file_has_anchor_pin(anchor))
    checks["abi_anchor_armed"] = {
        "ok": armed,
        "detail": (anchor.read_text().strip() if armed
                   else "ABI-anchor (numpy) pin UNRESOLVED — overlay installs run UNCONSTRAINED")}
    try:
        import importlib.metadata as _md
        checks["numpy_present"] = {"ok": True, "detail": f"numpy=={_md.version('numpy')}"}
    except Exception as e:  # noqa: BLE001
        checks["numpy_present"] = {"ok": False, "detail": f"numpy not resolvable: {e}"}
    # Accelerator consistency: a deployment that DECLARES a CUDA base (ABA_ACCELERATOR=
    # cuda, config.env) must actually have a CUDA-build torch — else GPU jobs silently
    # run on CPU on idle GPUs. Only checked when cuda is declared (a CPU deployment
    # legitimately has CPU-only torch).
    import os as _os
    if (_os.environ.get("ABA_ACCELERATOR") or "").strip().lower() == "cuda":
        _cuda = torch_cuda_build()
        checks["accelerator_cuda"] = {
            "ok": _cuda is not None,
            "detail": (f"torch CUDA build {_cuda}" if _cuda else
                       "ABA_ACCELERATOR=cuda but torch is CPU-only — GPU jobs would run on CPU "
                       "(rebuild the env)")}
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}


def gpu_capability_ok() -> tuple[Optional[bool], str]:
    """Can a GPU workload actually use a GPU in THIS interpreter? (via torch.cuda).
    Returns (ok, detail):
      True  — a usable CUDA GPU is visible;
      False — torch is present but sees NO usable GPU (a CPU-only build, or a CUDA
              build with no runtime/driver on this node) — a GPU job would silently
              run on CPU on an idle allocated GPU (the scVI-on-CPU incident);
      None  — torch isn't importable → not a torch GPU job, so don't judge it.
    The verify-at-use boundary: certainty about a remote node's accelerator can only
    be had ON that node, so this runs where the job runs (slurm_entry) and also backs
    the compute_env `gpu_usable` hint + the env_selfcheck invariant."""
    try:
        import torch  # noqa
    except Exception:  # noqa: BLE001 — no torch → not a torch-GPU job
        return None, "torch not importable"
    try:
        if torch.cuda.is_available():
            return True, f"torch {torch.__version__}, cuda {torch.version.cuda}"
        return False, (f"torch {torch.__version__} sees no usable GPU "
                       f"(version.cuda={torch.version.cuda}, cuda.is_available()=False)")
    except Exception as e:  # noqa: BLE001
        return False, f"torch.cuda probe errored: {e}"


def torch_cuda_build() -> Optional[str]:
    """The CUDA version torch was BUILT against (`torch.version.cuda`), or None if torch
    is a CPU-only build / not importable. Node-INDEPENDENT (a property of the build, not
    of runtime GPU visibility) — so ABA on a CPU login node can tell whether a GPU JOB on
    a compute node would be able to use the GPU, WITHOUT a GPU here. This is what backs
    the compute_env `gpu_usable` hint; the on-node `gpu_capability_ok` is the verify-at-use."""
    try:
        import torch  # noqa
        return torch.version.cuda
    except Exception:  # noqa: BLE001
        return None


_PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==")


def _freeze_pins(python_exe: Optional[str] = None) -> Optional[list[str]]:
    """Installed distributions of an interpreter as clean ``name==version`` constraint
    lines. Uses ``importlib.metadata`` (NOT ``pip freeze``): a conda-forge / local-wheel
    install — which pip freeze renders as ``name @ file://…``, an INVALID constraint that
    the old ``==``-only filter silently dropped — is still pinned by version. On a conda
    scientific base that drop meant numpy/scipy/scanpy vanished from the pins, turning the
    numpy-drift guard off. Metadata knows name+version regardless of install form. Editable/
    versionless dists are skipped (no usable pin). None on failure."""
    import json
    exe = python_exe or sys.executable
    code = (
        "import importlib.metadata as m, json\n"
        "seen={}\n"
        "for d in m.distributions():\n"
        "    n=(d.metadata.get('Name') or '').strip()\n"
        "    v=(d.version or '').strip()\n"
        "    if n and v: seen.setdefault(n.lower(), f'{n}=={v}')\n"
        "print(json.dumps(sorted(seen.values())))\n"
    )
    try:
        proc = subprocess.run([exe, "-c", code], capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        pins = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return None
    lines = [ln for ln in pins if _PIN_RE.match(ln)]     # keep only valid constraint lines
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
    # --ignore-installed: install the exact lock versions INTO the prefix without
    # uninstalling/recompiling the (now read-only, immutable) base copies — its job
    # is to fill a minimal install's overlay from the lock, independent of base.
    cmd = [sys.executable, "-m", "pip", "install", "--prefix", str(target), "--ignore-installed"]
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
    from core.exec.materialize import (project_pylib_dir, project_pylib_paths,
                                       tools_env, _site_paths)
    if project_id is None:
        from core import projects
        project_id = projects.current()

    # ── Python ── §11.4: just two tiers now — the immutable base (the install-wide
    # foundation; the old shared overlay was folded into it) + THIS project's
    # overlay, prepended so its versions win. The shared overlay is off the run
    # path and never written, so it's no longer a tier.
    base_site = sysconfig.get_path("purelib")
    py_layers = [
        {"tier": "base (immutable)", "scope": "installation", "delivery": "baked", "mutable": False,
         "path": base_site, "packages": _py_packages([base_site])},
    ]
    if project_id:
        py_layers.append(
            {"tier": "project overlay", "scope": "project", "project_id": project_id,
             "delivery": "on-demand", "mutable": True, "path": str(project_pylib_dir(project_id)),
             "packages": _py_packages([str(p) for p in project_pylib_paths(project_id)])})
    for name in iso.list_envs(project_id):
        py_layers.append(
            {"tier": "isolated", "scope": "capability", "delivery": "on-demand", "mutable": True,
             "name": name, "path": str(iso.env_dir(name, project_id)),
             "packages": _py_packages(_site_paths(iso.env_dir(name, project_id)))})
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
    _proot = iso._proj_root(project_id)
    if _proot.exists():
        iso_r += sorted(p for p in _proot.iterdir() if p.is_dir() and p.name.startswith("r-"))
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


def _base_site_dir() -> Path:
    import sysconfig
    return Path(sysconfig.get_path("purelib"))


# Known-harmless pip-check noise (optional extras that are intentionally absent).
_PIPCHECK_IGNORE = ("tensorboard", "scvi-tools")
_MISSING_RE = re.compile(r"requires (\S+?),? which is not installed", re.I)

# The lazy workflow imports a plain `import scanpy` SKIPS — the ones that bit the
# customer at sc.pp.neighbors (pandas→dateutil→six, numba/pynndescent/umap).
_DEEP_IMPORTS = ["pandas", "dateutil", "six", "sklearn", "numba", "pynndescent", "umap", "scipy.sparse"]


def base_health(*, deep: bool = True, python_exe: Optional[str] = None) -> dict:
    """Is the base .venv's dependency CLOSURE intact? `pip check` catches missing
    transitive deps (the `six` case `import scanpy` hides); ``deep`` also actually
    imports the lazy workflow deps (sc.pp.neighbors' import tree). Returns
    ``{ok, problems:[...], missing:[...]}`` — the read layer for self-heal +
    error surfacing."""
    exe = python_exe or sys.executable
    problems: list[str] = []
    try:
        proc = subprocess.run([exe, "-m", "pip", "check"], capture_output=True, text=True, timeout=60)
        for ln in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
            low = ln.lower().strip()
            if not low or any(g in low for g in _PIPCHECK_IGNORE):
                continue
            if "not installed" in low or "has requirement" in low:
                problems.append(ln.strip())
    except Exception as e:  # noqa: BLE001
        problems.append(f"pip check failed: {e}")
    if deep:
        script = "import " + ", ".join(_DEEP_IMPORTS) + "\nprint('ABA_DEEP_OK')"
        try:
            p2 = subprocess.run([exe, "-c", script], capture_output=True, text=True, timeout=120)
            if "ABA_DEEP_OK" not in (p2.stdout or ""):
                problems.append("deep import failed: " + ((p2.stderr or p2.stdout or "").strip())[-300:])
        except Exception as e:  # noqa: BLE001
            problems.append(f"deep import check failed: {e}")
    missing = sorted({m.group(1) for p in problems for m in [_MISSING_RE.search(p)] if m})
    return {"ok": not problems, "problems": problems, "missing": missing}


def set_base_writable(writable: bool) -> bool:
    """Flip the base site-packages writable/read-only (env_refactor.md immutable
    base). Read-only is the steady state — nothing should mutate the base at
    runtime; repair/rebuild flips it writable briefly. Best-effort."""
    site = _base_site_dir()
    if not site.exists():
        return False
    mode = "u+w" if writable else "a-w"
    try:
        subprocess.run(["chmod", "-R", mode, str(site)], capture_output=True, timeout=120)
        return True
    except Exception:  # noqa: BLE001
        return False


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


def repair_base(*, python_exe: Optional[str] = None) -> dict:
    """Self-heal a broken base: reinstall the missing closure FROM THE CANONICAL
    LOCK (so versions stay consistent). Flips the read-only base writable for the
    repair, then re-locks it. Returns ``{repaired, installed, error}``."""
    health = base_health()
    if health["ok"]:
        return {"repaired": False, "reason": "healthy"}
    exe = python_exe or sys.executable
    lock = ensure_base_constraints()
    # Reinstall the named-missing deps from the lock; if pip-check couldn't name
    # them (deep-import failure), reinstall the lock's full closure (idempotent —
    # only missing/broken packages actually re-download).
    targets = health["missing"]
    was_writable = set_base_writable(True)
    try:
        cmd = [exe, "-m", "pip", "install", "--no-input"]
        if lock:
            cmd += ["-c", str(lock)]
        if targets:
            cmd += targets
        elif lock:
            cmd += ["-r", str(lock)]   # whole closure from the lock
        else:
            return {"repaired": False, "error": "broken but no lock + no named missing deps"}
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        ok = proc.returncode == 0
    finally:
        set_base_writable(False)   # restore read-only steady state
    after = base_health()
    return {"repaired": after["ok"], "installed": targets or "lock-closure",
            "still_broken": after["problems"] if not after["ok"] else [],
            "error": None if ok else (proc.stderr or proc.stdout or "")[-800:]}


# ─── verify-once / skip-while-unchanged (avoid the ~9s deep check every boot) ──
# The base is an IMMUTABLE foundation: once we've deep-verified it and frozen it
# read-only, it cannot change until a rebuild. So re-running the full deep import
# check on every startup is pure waste — it scales with the base's file count
# (84k+ once the R/scientific stack lands) and dominates startup latency. We
# stamp a fingerprint after a clean verify and skip the deep check while it holds.

def base_is_readonly_fs() -> bool:
    """True if the base lives on a read-only *filesystem* (not just chmod'd
    read-only). The SIF/OOD case: the base is baked into a read-only squashfs
    image and was already verified at build time, so per-launch re-verification
    (and set_base_writable) is both pointless and impossible. Distinct from our
    own `a-w` chmod, which leaves the FS writable — hence statvfs, not os.access."""
    try:
        st = os.statvfs(_base_site_dir())
        return bool(st.f_flag & os.ST_RDONLY)
    except Exception:  # noqa: BLE001
        return False


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
    - WRAPPED offload (`ABA_JOB_WRAP=sif`, a fat SIF): the job RE-ENTERS the image
      via `apptainer exec` (slurm_submitter._job_body), so the baked in-image base is
      exactly what runs — a node-local/in-image base is CORRECT, not a defect. Fat is
      NOT single-node in this mode (misc/fatagain.md).

    Fires only for the 'slurm' submitter."""
    from core.jobs.submitter import submitter_name
    if submitter_name() != "slurm":
        return {"ok": True, "severity": "info", "detail": "local submitter — base sharing N/A"}
    # Fat + job-wrap: offloaded env-jobs re-enter the SIF, so the baked base is reachable
    # (its being node-local/in-image is by design). Don't flag it as unreachable.
    if (os.environ.get("ABA_JOB_WRAP") or "").strip().lower() == "sif":
        return {"ok": True, "severity": "info",
                "detail": ("fat SIF + job-wrap (ABA_JOB_WRAP=sif): offloaded env-jobs re-enter the "
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


def _base_stamp_path() -> Path:
    """Where the 'base verified' stamp lives — under the mutable runtime root, NOT
    the (read-only) base. Independent per install / per OOD tenant."""
    from core.config import RUNTIME_DIR
    return Path(str(RUNTIME_DIR)) / "base-verified.json"


def base_fingerprint() -> str:
    """Cheap identity of the current base: the canonical lock's content + the
    base site-dir's mtime (changes when packages are added/removed) + the
    interpreter. Stable across our read-only chmod (which doesn't alter dir
    mtime), changes whenever the base is rebuilt or repaired."""
    h = hashlib.sha256()
    try:
        lock = canonical_lock_path() or base_constraints_path()
        if lock and lock.exists():
            h.update(lock.read_bytes())
    except Exception:  # noqa: BLE001
        pass
    try:
        site = _base_site_dir()
        h.update(str(site).encode())
        h.update(str(site.stat().st_mtime_ns).encode())
    except Exception:  # noqa: BLE001
        pass
    h.update(sys.version.encode())
    h.update((sys.executable or "").encode())
    return h.hexdigest()


def _verified_stamp_matches(fp: str) -> bool:
    try:
        data = json.loads(_base_stamp_path().read_text())
        return data.get("fingerprint") == fp
    except Exception:  # noqa: BLE001
        return False


def _write_verified_stamp(fp: str) -> None:
    try:
        p = _base_stamp_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"fingerprint": fp}))
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        pass


def self_heal_base(*, log: Callable[[str], None] = print) -> dict:
    """Startup base self-heal, but only when it can actually matter:

    - read-only FS (SIF/OOD immutable image) → skip; verified at build time.
    - fingerprint stamp current → skip; base is immutable and unchanged since
      its last clean deep verify.
    - otherwise → deep verify, repair from the lock if broken, refreeze read-only,
      and stamp. Meant to run in a BACKGROUND thread so startup-to-ready isn't
      blocked on the ~9s deep import (env_root_cause covers the brief first-boot
      window where a kernel could spawn before this completes)."""
    site = _base_site_dir()
    if not site.exists():
        return {"skipped": "no-base"}
    # Env self-check (cheap, ALWAYS runs — even when the deep base verify below is
    # skipped): arm + verify the ABI-anchor guard. This is the invariant the deep
    # closure check does NOT cover; a silently-off anchor let unconstrained overlay
    # installs rebuild numpy (the conda '@ file://' base case).
    try:
        sc = env_selfcheck()
        if sc["ok"]:
            log("[startup] env self-check ok — ABI anchor armed (" +
                sc["checks"]["abi_anchor_armed"]["detail"] + ")")
        else:
            bad = {k: v["detail"] for k, v in sc["checks"].items() if not v["ok"]}
            log(f"[startup] ENV SELF-CHECK PROBLEM: {bad}")
    except Exception as e:  # noqa: BLE001 — a check must never block startup
        log(f"[startup] env self-check errored (non-fatal): {e}")
    if base_is_readonly_fs():
        log("[startup] base on read-only filesystem (immutable image) — skipping deep verify")
        return {"skipped": "readonly_fs"}
    fp = base_fingerprint()
    if _verified_stamp_matches(fp):
        log("[startup] base unchanged since last verify — skipping deep check")
        return {"skipped": "stamp"}
    h = base_health(deep=True)
    repaired = None
    if not h["ok"]:
        repaired = repair_base()
        log(f"[startup] base was broken {h['problems'][:3]} -> repair: {repaired}")
        h = base_health(deep=True)
    else:
        log("[startup] base health: ok (deep)")
    if set_base_writable(False):
        log("[startup] base set read-only (immutable foundation)")
    if h["ok"]:
        _write_verified_stamp(base_fingerprint())   # recompute: repair may have changed it
        log("[startup] base verified — stamped to skip next boot's deep check")
    return {"ok": h["ok"], "repaired": repaired}


_BASE_HEALTH_TS = 0.0


def ensure_base_healthy(*, throttle_s: int = 300) -> dict:
    """Throttled check-and-repair, for the kernel-spawn / startup path. Skips the
    (subprocess) check if one ran within ``throttle_s``. Returns the health/repair
    summary, or ``{skipped:True}``."""
    global _BASE_HEALTH_TS
    import time as _t
    now = _t.monotonic()
    if now - _BASE_HEALTH_TS < throttle_s:
        return {"skipped": True}
    _BASE_HEALTH_TS = now
    h = base_health(deep=False)   # fast pip-check; catches the missing-dep (six) case
    if h["ok"]:
        return {"ok": True}
    return {"ok": False, "repair": repair_base(), "was": h["problems"]}


# Surface patterns: a run failing this way is *likely* an env break, not user code.
_ENV_FAIL_RE = re.compile(
    r"numpy\.core\.multiarray failed to import|failed to import|"
    r"ModuleNotFoundError|cannot import name|undefined symbol|"
    r"DLL load failed|partially initialized module|circular import", re.I)


def env_root_cause(stderr: str, *, repair: bool = True) -> Optional[dict]:
    """Translate a cryptic import/ABI traceback into the BASE root cause — the
    "what surfaces is the first thing that fails" fix. Returns None for ordinary
    code errors (base intact) so normal failures aren't touched; otherwise a
    surfaced note + (optionally) the result of an auto-repair. The deep call is
    gated by the regex, so it only runs on import-shaped failures."""
    if not stderr or not _ENV_FAIL_RE.search(stderr):
        return None
    h = base_health(deep=False)
    if h["ok"]:
        return None   # base closure intact — it's the user's own missing import
    note = ("The base environment is broken (not your code): "
            + "; ".join(h["problems"][:3]))
    rep = repair_base() if repair else {"repaired": False, "reason": "repair disabled"}
    if rep.get("repaired"):
        note += f". Auto-repaired ({rep.get('installed')}) — re-run the cell."
    else:
        note += ". Auto-repair did not fully succeed; the environment needs attention."
    return {"note": note, "base_problems": h["problems"], "repair": rep}


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
