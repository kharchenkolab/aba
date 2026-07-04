"""core.exec.modules — HPC environment modules (Lmod / Tcl) as a capability provider.

CLUSTER INSTALLS ONLY. Active iff the batch submitter is Slurm
(`ABA_BATCH_SUBMITTER=slurm`, set by the cluster-personal installer and by OOD's
`before.sh`) AND a `module` init script is sourceable. A hard no-op everywhere
else — on a local/Mac install `modules_active()` is False and nothing here runs.

Dynamic + cached, lazy (incl. at install — nothing is prebuilt): the module
catalog is discovered on first use via `module -t avail` (~0.2s here, cache-backed
by Lmod) and cached under `ENVS_DIR/modules/catalog.json`, keyed by a MODULEPATH
signature + TTL. Tier-1 matching (this file) is a deterministic exact-normalized-
name resolve (`samtools` → `samtools/1.10-foss-2018b`); it only fires on a confident
name match and otherwise returns None (caller falls through to pip/conda/CRAN). The
LLM matcher is Phase 2. See misc/cluster_modules.md.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

# Env vars a `module load` typically mutates that matter for executing tools.
# PYTHONPATH / PYTHONHOME are deliberately EXCLUDED: a module's Python path would
# shadow the conda env's site-packages (the prj_6d986f40 numpy incident), and the
# whole module contract here is "binaries only, never a module's Python libs" (see
# kernel_env_snippet / module_env_overlay, and the Slurm job's post-`module load`
# python sanitize in SlurmSubmitter). Only binary/library search paths are captured.
_PATH_VARS = ("PATH", "LD_LIBRARY_PATH", "CPATH",
              "LIBRARY_PATH", "PKG_CONFIG_PATH", "MANPATH")
_SCALAR_VARS = ("CUDA_HOME",)
_SAFE_MODULE = re.compile(r"^[A-Za-z0-9_./+-]+$")          # guards shell interpolation
# terse `module -t avail` line: tool/version[-toolchain]; dir headers end in ':' (no match)
_AVAIL_LINE = re.compile(r"^([A-Za-z0-9_.+-]+)/([0-9][^/\s]*)$")
_AVAIL_MARK = re.compile(r"\s*\(\w+\)\s*$")               # trailing (D)/(default)/(L) marker
_TTL_S = 6 * 3600
_MEMO: dict = {}                                          # process-local: {"sig":.., "cat":..}


# ── config + gating ─────────────────────────────────────────────────────────
def _mod_cfg() -> dict:
    """The `modules:` block of hpc.yaml (prefer/discovery/init_path). {} if absent."""
    try:
        from core.jobs.hpc_config import hpc_config
        return dict(hpc_config().get("modules") or {})
    except Exception:  # noqa: BLE001
        return {}


def _init_script() -> str | None:
    """A sourceable module-init script, or None. Honors hpc.yaml `init_path` /
    $ABA_LMOD_INIT, else the same candidate list as install/linux/setup.sh."""
    cfg = _mod_cfg()
    cands = [cfg.get("init_path") or os.environ.get("ABA_LMOD_INIT") or "",
             os.environ.get("LMOD_PKG", "") + "/init/bash" if os.environ.get("LMOD_PKG") else "",
             os.environ.get("MODULESHOME", "") + "/init/bash" if os.environ.get("MODULESHOME") else "",
             "/etc/profile.d/lmod.sh", "/etc/profile.d/z00_lmod.sh", "/etc/profile.d/modules.sh"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _is_cluster() -> bool:
    try:
        from core.jobs.submitter import submitter_name
        return submitter_name() == "slurm"
    except Exception:  # noqa: BLE001
        return os.environ.get("ABA_BATCH_SUBMITTER", "").strip().lower() == "slurm"


def modules_active() -> bool:
    """The single gate. True iff: a Slurm (cluster) install + a usable module
    system + not disabled. Everything else early-returns on this, so the provider
    is inert on local installs and when no module system is present."""
    if os.environ.get("ABA_MODULES_ENABLED") == "0":
        return False
    if (_mod_cfg().get("prefer") or "").lower() == "off":
        return False
    return _is_cluster() and _init_script() is not None


def _module_sh(snippet: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run `snippet` in a non-login bash that has `module` defined (sources init)."""
    init = _init_script()
    script = (f". {init} >/dev/null 2>&1\n" if init else "") + snippet
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)


# ── discovery (lazy + cached) ────────────────────────────────────────────────
def _cache_file() -> Path:
    from core.config import ENVS_DIR
    return Path(str(ENVS_DIR)) / "modules" / "catalog.json"


def _modulepath_sig() -> str:
    """Cheap signature of MODULEPATH (dirs + mtimes) — changes when admins add/retire."""
    out = []
    for d in (os.environ.get("MODULEPATH", "") or "").split(":"):
        if not d:
            continue
        try:
            out.append(f"{d}@{int(os.path.getmtime(d))}")
        except OSError:
            out.append(f"{d}@0")
    return "|".join(out)


def _parse_avail(text: str) -> dict:
    """`module -t avail` terse output → {tool_lower: [{full,tool,version,toolchain}]}."""
    cat: dict[str, list] = {}
    for raw in text.splitlines():
        m = _AVAIL_LINE.match(_AVAIL_MARK.sub("", raw.strip()))
        if not m:
            continue
        tool, verfull = m.group(1), m.group(2)
        version, _, toolchain = verfull.partition("-")
        cat.setdefault(tool.lower(), []).append(
            {"full": f"{tool}/{verfull}", "tool": tool, "version": version, "toolchain": toolchain})
    return cat


def _discover() -> dict:
    """Build the catalog from the scheduler. Phase 1: terse `avail` (fast, cache-
    backed). `discovery: spider` (full hierarchy) is a later option."""
    try:
        r = _module_sh("module -t avail 2>&1", timeout=120)
        return _parse_avail(r.stdout)
    except Exception:  # noqa: BLE001
        return {}


def catalog(refresh: bool = False) -> dict:
    """Discovered module catalog {tool_lower: [entries]}, cached (process memo →
    file cache keyed by MODULEPATH sig + TTL → rebuild). Empty when inactive."""
    if not modules_active():
        return {}
    sig = _modulepath_sig()
    if not refresh and _MEMO.get("sig") == sig:
        return _MEMO["cat"]
    cf = _cache_file()
    if not refresh:
        try:
            blob = json.loads(cf.read_text())
            if blob.get("sig") == sig and (time.time() - blob.get("ts", 0)) < _TTL_S:
                _MEMO.update(sig=sig, cat=blob["cat"])
                return blob["cat"]
        except Exception:  # noqa: BLE001
            pass
    cat = _discover()
    _MEMO.update(sig=sig, cat=cat)
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"sig": sig, "ts": time.time(), "cat": cat}))
    except Exception:  # noqa: BLE001
        pass
    return cat


# ── Tier-1 matching (deterministic; confident name-equality or nothing) ──────
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _ver_key(v: str):
    """Comparable version key tolerant of alpha parts (2.7.1a, 1.0rc) — no mixed
    int/str compares (each element is a homogeneous tuple)."""
    key = []
    for p in re.split(r"[._\-]+", v or ""):
        m = re.match(r"^(\d+)(.*)$", p)
        key.append((1, int(m.group(1)), m.group(2)) if m else (0, 0, p))
    return key


def _best_match(cat: dict, tool: str) -> str | None:
    """Pure Tier-1 match over a given catalog — newest module whose normalized name
    EXACTLY equals `tool`, else None. Separated from `resolve` so it's testable
    without a live cluster."""
    want = _norm(tool)
    hits = [e for tl, entries in cat.items() if _norm(tl) == want for e in entries]
    if not hits:
        return None
    return max(hits, key=lambda e: _ver_key(e["version"]))["full"]


def resolve(tool: str) -> str | None:
    """Tier-1: the newest module whose normalized name EXACTLY equals `tool`, or
    None. Conservative by design — `bwa` never matches `bwa-meth`, a missing tool
    returns None so the caller falls through to the build path. No guessing."""
    if not tool or not modules_active():
        return None
    return _best_match(catalog(), tool)


# ── env-delta capture (for in-process kernels; jobs use `module load` in job.sh) ─
def _parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            out[k] = v
    return out


def _delta(before: dict, after: dict) -> dict:
    """What `module load` added: for PATH-like vars the list of newly-prepended
    entries (to prepend onto a kernel's own value); for scalars the set value."""
    out: dict = {}
    for var in _PATH_VARS:
        b, a = before.get(var, ""), after.get(var, "")
        if a != b:
            bset = set(b.split(":"))
            added = [p for p in a.split(":") if p and p not in bset]
            if added:
                out[var] = added
    for var in _SCALAR_VARS:
        if after.get(var) and after.get(var) != before.get(var):
            out[var] = after[var]
    return out


def env_delta(full_module: str) -> dict:
    """The environment overlay a `module load <full_module>` produces, as
    {PATH-var: [prepend entries], SCALAR: value}. {} if the load fails or the name
    looks unsafe (caller treats {} as 'module didn't satisfy → fall through')."""
    if not full_module or not modules_active() or not _SAFE_MODULE.match(full_module):
        return {}
    snippet = ("env\necho __ABA_MID__\n"
               f"module load {full_module} 2>/dev/null || {{ echo __ABA_LOADFAIL__; exit 0; }}\n"
               "env\n")
    try:
        r = _module_sh(snippet, timeout=60)
    except Exception:  # noqa: BLE001
        return {}
    if "__ABA_LOADFAIL__" in r.stdout or "__ABA_MID__" not in r.stdout:
        return {}
    before_txt, _, after_txt = r.stdout.partition("__ABA_MID__")
    return _delta(_parse_env(before_txt), _parse_env(after_txt))


def load_lines(mods: list[str]) -> str:
    """Bash prologue that loads `mods` in a non-login Slurm job script: source the
    module init (job.sh is not a login shell, so `module` is undefined), then
    `module load`. Returns '' when inactive, no mods, or no init — so injecting it
    is always safe. Unsafe names are dropped (guards shell interpolation).

    NOTE: a raw `module load` brings the module's FULL env, including its own Python
    if it has one. A caller that then runs the conda-env python (the Slurm job script)
    MUST sanitize the interpreter env afterward — clear PYTHONHOME + reset PYTHONPATH —
    so a module's Python can't shadow the env (the prj_6d986f40 incident; see
    SlurmSubmitter.submit)."""
    if not mods or not modules_active():
        return ""
    init = _init_script()
    safe = [m for m in mods if m and _SAFE_MODULE.match(m)]
    if not init or not safe:
        return ""
    return f". {init} >/dev/null 2>&1\nmodule load {' '.join(safe)}\n"


# ── per-project module set (job-path scope: background jobs load these) ───────
# A module whose NAME bundles a Python toolchain (e.g.
# 'scanpy/1.4.4-foss-2018b-python-3.6.6', 'Python/3.6.6') carries its OWN Python.
# `module load`ed into a background job that runs the conda-env python, it shadows
# the env's site-packages (the prj_6d986f40 numpy-1.17.3 incident). Such modules are
# never recorded or job-loaded: pip libraries live in the conda env, and a binary
# tool never needs a python-toolchain module.
_PY_MODULE_RE = re.compile(r"(?:^|[-/])python[-/_]?\d", re.I)


def _is_python_module(module_full: str) -> bool:
    return bool(module_full) and bool(_PY_MODULE_RE.search(module_full))


def _project_modules_file(project_id: str) -> Path:
    from core.data.workspace import _project_work_root
    return _project_work_root(str(project_id or "default")) / "modules.json"


def record_project_module(project_id: str, module_full: str) -> None:
    """Add a resolved module to the project's set so its background Slurm jobs
    `module load` it — `ensure_capability` records here, `SlurmSubmitter` reads it.
    No-op off-cluster / for an unsafe name."""
    if not module_full or not _SAFE_MODULE.match(module_full) or not modules_active():
        return
    if _is_python_module(module_full):     # never job-load a python-toolchain module (shadows the conda env)
        return
    f = _project_modules_file(project_id)
    try:
        cur = set(json.loads(f.read_text())) if f.exists() else set()
    except Exception:  # noqa: BLE001
        cur = set()
    if module_full in cur:
        return
    cur.add(module_full)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(sorted(cur)))
    except Exception:  # noqa: BLE001
        pass


def project_modules(project_id: str) -> list[str]:
    """Modules recorded for a project's background jobs (deduped, sorted). [] if
    none — a pure file read, safe to call anywhere (the submitter unions it in).
    Self-heals: any python-toolchain module recorded before the guard existed is
    dropped here, so it never gets job-loaded (it would shadow the conda env)."""
    try:
        mods = sorted(set(json.loads(_project_modules_file(project_id).read_text())))
    except Exception:  # noqa: BLE001
        return []
    return [m for m in mods if not _is_python_module(m)]


def module_env_overlay(full_module: str, base_env: dict | None = None) -> dict:
    """Concrete env-var overlay for `module load <full_module>`, ready to merge into an
    exec env: PATH-like vars are prepended onto `base_env`'s current value; scalars are set.
    {} if the module yields no delta / is inactive. This is how an IN-PROCESS run (an inline
    job with no job.sh `module load`) gets the tool's binary on PATH — the counterpart to
    load_lines() for Slurm job scripts."""
    d = env_delta(full_module)
    if not d:
        return {}
    env = os.environ if base_env is None else base_env
    out: dict = {}
    for var, val in d.items():
        if isinstance(val, list):                # PATH-like → prepend our entries
            cur = env.get(var, "")
            out[var] = ":".join(val) + ((":" + cur) if cur else "")
        else:                                    # scalar → set
            out[var] = val
    return out


def kernel_env_snippet(full_module: str) -> str:
    """Python that prepends `full_module`'s env-delta to os.environ in a RUNNING
    kernel, so subprocesses spawned by run_python find the tool's binary IN-PROCESS
    (no background job, no kernel restart). '' if the module yields no delta / is
    inactive. For binary-tool use only — we never make a kernel import a module's
    Python libs (that's the fragile interpreter-linking case)."""
    d = env_delta(full_module)
    if not d:
        return ""
    lines = ["import os as _o"]
    for var, val in d.items():
        if isinstance(val, list):                        # PATH-like → prepend our entries
            add = ":".join(val)
            lines.append(f"_o.environ[{var!r}] = {add!r} + (':'+_o.environ[{var!r}] "
                         f"if _o.environ.get({var!r}) else '')")
        else:                                            # scalar → set
            lines.append(f"_o.environ[{var!r}] = {val!r}")
    return "\n".join(lines)
