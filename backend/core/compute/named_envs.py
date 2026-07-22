"""Per-project NAMED weft environments — the W1 cutover of the isolated-env lane
(misc/weft_rewrite.md §4b; replaces core/exec/isolated_env.py's uv/venv+R-lib
machinery).

A named env is an aba-side handle: per-project ``name → EnvID`` in
``PROJECTS_DIR/<pid>/weft_envs.json`` (project state — travels with the
project; EnvIDs re-realize anywhere from weft's lock). weft owns everything
below the handle: solving, realization, integrity, rebuild-after-GC.

The doctrine shift vs the old machinery: **never install into an existing
env.** "Add a package" = ``extends_env`` over the current EnvID → a NEW EnvID
(O(delta) overlay realization), and the handle moves. History is kept so
provenance stays honest — an exec record's EnvID always names exactly the set
it ran with.

Sync by design: these are called from tool worker threads (the guide runs tools
via run_in_executor) and from the one-shot run path. ``_sync`` bridges to the
async port; calling from the event-loop thread is a bug (it would block the
loop on a solve) and raises.

W1 scope: the named lane only — the DEFAULT project env stays on the served
base until the controller-SIF deploy model lands (W3 re-sequencing, agreed
2026-07-14).
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from core.compute import adapter as _adapter
from core.compute.errors import ComputeError

# Names that mean "the normal served stack", never a named env (kept verbatim
# from the retired isolated_env module — the tool contract depends on them).
RESERVED_ENV_NAMES = frozenset({"default", "base", "shared", "project"})


def is_reserved_name(name: str) -> bool:
    return (name or "").strip().lower() in RESERVED_ENV_NAMES


def _sync(coro):
    """Run a port coroutine from a worker thread (adapter.run_sync — loud on
    the loop thread; blocking the event loop on a conda solve is never
    acceptable)."""
    try:
        return _adapter.run_sync(coro)
    except RuntimeError as e:
        if "worker-thread-only" in str(e):
            raise RuntimeError(
                "named_envs is sync-only: call from a worker thread, "
                "not the event loop (use the async port directly there)") from e
        raise


# ── the per-project registry ─────────────────────────────────────────────────
# weft_envs.json holds THREE namespaces (envs / active / default) written by BOTH
# named_envs and project_env. A plain load-modify-save loses updates when two env
# operations interleave (parallel tool calls, or a named-env create racing the
# default-session write — observed live: an isolated env vanished under a
# concurrent default-session write). So all writes go through `_update`, which
# serializes on a process lock and writes atomically (temp + os.replace). The
# SLOW part (the weft solve) stays OUTSIDE the lock; only the fast file mutation
# is held.
import os as _os
import threading as _threading

_REGISTRY_LOCK = _threading.RLock()


def _registry_path(project_id: str) -> Path:
    from core.config import PROJECTS_DIR
    return PROJECTS_DIR / str(project_id) / "weft_envs.json"


def _load(project_id: str) -> dict:
    p = _registry_path(project_id)
    if not p.exists():
        return {"envs": {}, "active": {}, "default": {}}
    try:
        data = json.loads(p.read_text()) or {}
    except Exception:  # noqa: BLE001
        return {"envs": {}, "active": {}, "default": {}}
    data.setdefault("envs", {})
    data.setdefault("active", {})
    data.setdefault("default", {})
    return data


def _save(project_id: str, data: dict) -> None:
    """Atomic write (temp + replace). Prefer `_update` for read-modify-write —
    a bare _save can still clobber a concurrent update."""
    p = _registry_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{_os.getpid()}.{_threading.get_ident()}")
    try:
        tmp.write_text(json.dumps(data, indent=1))
        _os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()          # no-op after a successful replace; cleans up on failure
        except OSError:
            pass


def _update(project_id: str, mutator):
    """Serialized read-modify-write of weft_envs.json: lock → load fresh →
    mutator(data) → atomic save. The mutator sees the LATEST on-disk state, so
    concurrent env operations merge instead of clobbering. Returns the mutator's
    return value (or the saved data if it returns None)."""
    with _REGISTRY_LOCK:
        data = _load(project_id)
        ret = mutator(data)
        _save(project_id, data)
        return ret if ret is not None else data


def list_names(project_id: str) -> list[str]:
    return sorted(_load(project_id)["envs"])


def resolve(project_id: str, name: str) -> Optional[dict]:
    """The env row {env_id, language, packages, history, …} or None."""
    return _load(project_id)["envs"].get((name or "").strip())


def get_active(project_id, lang: str = "python") -> str:
    if not project_id:
        return "default"
    return _load(str(project_id))["active"].get(lang) or "default"


def set_active(project_id: str, name: str, lang: str = "python") -> dict:
    """Bind `name` as the active env for `lang`'s lane. Reserved names
    ('default', …) reset the pointer to the served stack. A real name must
    exist and match the slot's language — a silent cross-language binding
    would point one language's lane at another language's env."""
    name = (name or "").strip()
    if not is_reserved_name(name):
        row = resolve(project_id, name)
        if row is None:
            raise ComputeError(
                "unknown_env", f"no named env {name!r} in this project",
                stage="aba", hints={"available": list_names(project_id)})
        row_lang = row.get("language") or "python"
        if row_lang != lang:
            raise ComputeError(
                "env.language_mismatch",
                f"{name!r} is a {row_lang} env — it cannot be the active "
                f"{lang} env", stage="aba")
    _update(project_id, lambda data: data["active"].__setitem__(lang, name))
    return {"lang": lang, "active": name}


def resolve_env(project_id, language: str, explicit=None) -> Optional[str]:
    """THE selection policy for "which env runs language-L code in this
    project" — every execution lane resolves through here (census guard:
    tests/test_env_resolution.py). Returns a named-env NAME, or None for the
    default served stack.

    Precedence: an EXPLICIT request wins ('' and reserved names normalize to
    None; any other string passes through verbatim — existence checks and
    lane sentinels like 'system' stay with the lane); else the project's
    ACTIVE pointer for that language; else None. A dangling pointer (names an
    env that no longer exists — forget() clears pointers itself, so only
    corruption/manual edits get here) falls back to the default session with
    a printed warning rather than erroring a bare run that never asked for an
    env."""
    if explicit is not None:
        name = str(explicit).strip()
        return None if (not name or is_reserved_name(name)) else name
    if not project_id:
        return None
    pid = str(project_id)
    name = get_active(pid, language)
    if not name or is_reserved_name(name):
        return None
    if resolve(pid, name) is None:
        print(f"[named_envs] active {language} env {name!r} no longer exists "
              f"in project {pid} — falling back to the default session",
              flush=True)
        return None
    return name


# ── create / extend ──────────────────────────────────────────────────────────

def _spec_for(project_id: str, name: str, language: str,
              packages: list[str], python_version: Optional[str] = None,
              conda_packages: Optional[list[str]] = None) -> dict:
    """A fresh named-env spec. Python envs bake ipykernel and R envs bake
    r-irkernel so the per-env persistent kernel works without ever installing
    into the frozen env (an env without its kernel package is one-shot-only —
    useless for stateful work); R envs also get the cran layer for CRAN-style
    specs. `python_version` (e.g. "3.10")
    pins the interpreter — the whole point of an isolated env is often a
    DIFFERENT python than the base (an old package that needs <3.11); a fresh
    env has no frozen base to conflict, so weft picks the matching build.
    `conda_packages` routes wheel-less conda-only deps into the conda layer —
    the eco passthrough the cold-base lever advertises (a python env's
    `packages` are pypi; without this a conda-only package could never be
    provisioned through the isolated lane)."""
    label = f"aba-{project_id}-{name}"
    if language == "r":
        conda = [p for p in packages if p.startswith(("r-", "bioconductor-"))]
        cran = [p for p in packages if not p.startswith(("r-", "bioconductor-"))]
        _all_conda = [*conda, *(conda_packages or [])]
        kern = ([] if any(p.split()[0] == "r-irkernel" for p in _all_conda)
                else ["r-irkernel"])
        # r-base: caller-constraint-wins. A caller-PINNED r-base replaces the
        # baked default (a different R is the point of an isolated env — the
        # exact analogue of python_version); a bare 'r-base' dedupes away.
        # Splicing both emitted a duplicate spec, which the substrate now
        # refuses at intake — truthfully, but with no lever the agent holds.
        def _is_rbase(p: str) -> bool:
            return p.split()[0].split("=")[0].strip() == "r-base"
        _caller_rbase = [p for p in _all_conda if _is_rbase(p)]
        _all_conda = [p for p in _all_conda if not _is_rbase(p)]
        _pinned = next((p for p in _caller_rbase if p.strip() != "r-base"), None)
        _rbase = _pinned or "r-base =4.4.*"
        deps: dict = {"conda": [_rbase, *kern, *_all_conda]}
        if cran:
            deps["cran"] = cran
        return {"name": label, "deps": deps}
    pyspec = f"python ={python_version}" if python_version else "python =3.12"
    return {"name": label,
            "deps": {"conda": [pyspec, "ipykernel", *(conda_packages or [])],
                     "pypi": list(packages)}}


def create(project_id: str, name: str, *, language: str = "python",
           packages: list[str] | None = None,
           python_version: Optional[str] = None,
           conda_packages: list[str] | None = None) -> dict:
    """Solve a fresh named env → EnvID (realization is lazy — first run
    realizes). Raises ComputeError with weft's structured cause (e.g.
    env.solve_conflict names the minimal conflicting set). `conda_packages`
    is the eco passthrough for conda-only deps (see _spec_for)."""
    name = name.strip()
    spec = _spec_for(project_id, name, language, packages or [], python_version,
                     conda_packages)
    res = _sync(_adapter.get_compute().env_ensure(spec))     # slow — OUTSIDE the lock
    now = time.time()
    # base_spec/python_version/layers persist HOW the env was built — the
    # platform re-lock (ensure_platform) re-solves from these; reconstructing
    # from the flat package list silently dropped a python pin (3.10 → the
    # 3.12 default) and flattened extend() layering.
    _update(project_id, lambda data: data["envs"].__setitem__(name, {
        "env_id": res["env_id"], "language": language,
        "packages": list(packages or []),
        "conda_packages": list(conda_packages or []), "history": [],
        "python_version": python_version, "base_spec": spec, "layers": [],
        "created_at": now, "updated_at": now,
    }))
    return {"env_id": res["env_id"], "status": res.get("status"),
            "summary": res.get("summary"), "engine": "weft"}


def _extend_deps(language: str, packages: list[str],
                 eco: Optional[str] = None) -> dict:
    """The deps block for an extend layer. Explicit `eco` routes everything
    one way (the passthrough for conda-only python deps — the cold-base
    lever's consumer side); otherwise python → pypi and R splits by the same
    prefix heuristic create() uses (`r-`/`bioconductor-` → conda, else cran —
    extend used to force cran, which stranded conda-only R packages)."""
    if eco:
        return {eco: list(packages)}
    if language == "r":
        conda = [p for p in packages if p.startswith(("r-", "bioconductor-"))]
        cran = [p for p in packages if not p.startswith(("r-", "bioconductor-"))]
        deps: dict = {}
        if conda:
            deps["conda"] = conda
        if cran:
            deps["cran"] = cran
        return deps
    return {"pypi": list(packages)}


def _layer_deps(layer, language: str) -> dict:
    """Normalize a recorded layer for re-lock replay: new layers store their
    full deps block ({'deps': {eco: [...]}}); legacy layers are flat package
    lists routed to the language default (pypi / cran) — exactly what extend
    did when they were recorded."""
    if isinstance(layer, dict):
        return {k: list(v) for k, v in (layer.get("deps") or {}).items()}
    return {("cran" if language == "r" else "pypi"): list(layer)}


def extend(project_id: str, name: str, packages: list[str], *,
           eco: Optional[str] = None) -> dict:
    """Add packages = extends_env over the current EnvID → NEW EnvID, handle
    moves, old id kept in history (never install into a frozen env). `eco`
    overrides the ecosystem routing (see _extend_deps).

    Concurrency: the solve is slow and runs OUTSIDE the registry lock, so the
    handle can move underneath (another extend / platform re-lock landing
    first). The old code overwrote `env_id` unconditionally — the LAST writer
    won and the first extend's delta silently vanished from the identity chain
    (its layer stayed recorded, so a later re-lock resurrected it: identity
    and record disagreed). Optimistic retry instead: apply only if the parent
    we solved against is still the tip; otherwise re-solve on the new tip —
    both deltas end up in the chain, in landing order."""
    deps_probe = _extend_deps(
        (resolve(project_id, name) or {}).get("language") or "python",
        packages, eco)
    if not deps_probe:
        raise ComputeError("task.invalid", "nothing to install", stage="aba")
    for _attempt in range(3):
        row = resolve(project_id, name)
        if row is None:
            raise ComputeError("unknown_env",
                               f"no named env {name!r} in this project",
                               stage="aba", hints={"available": list_names(project_id)})
        # Idempotent re-extend: every requested spec string already recorded →
        # answer the CURRENT identity with no re-solve. The old behavior minted
        # a new EnvID (and the tool layer then evicted the env's live kernels)
        # for a no-op request — a retried/duplicated call was destructive. An
        # exact-string check only: a changed pin ("pkg==2.0" vs "pkg") is a
        # REAL change and re-solves.
        if set(packages) <= set(row.get("packages") or []):
            return {"env_id": row["env_id"], "status": "cached",
                    "summary": "all requested packages already recorded — "
                               "no re-solve", "delta": []}
        deps = _extend_deps(row["language"], packages, eco)
        spec = {"name": f"aba-{project_id}-{name}",
                "extends_env": row["env_id"], "deps": deps}
        res = _sync(_adapter.get_compute().env_ensure(spec))  # slow — OUTSIDE the lock
        applied = {"ok": False}

        def _apply(data):
            r = data["envs"].get(name)
            if r is None:   # vanished concurrently — re-seed from the solved id
                r = {"env_id": row["env_id"], "language": row["language"],
                     "packages": list(row["packages"]), "history": []}
                data["envs"][name] = r
            elif r.get("env_id") != row["env_id"]:
                return      # tip moved under our solve — retry on the new tip
            r.setdefault("history", []).append(r["env_id"])
            r["env_id"] = res["env_id"]
            r["packages"] = list(dict.fromkeys([*r.get("packages", []), *packages]))
            # layers carry their FULL deps block so a platform re-lock replays
            # the same ecosystems (a flat list replayed as pypi would mis-route
            # a conda layer)
            r.setdefault("layers", []).append({"deps": deps})
            r["updated_at"] = time.time()
            applied["ok"] = True
        _update(project_id, _apply)
        if applied["ok"]:
            return {"env_id": res["env_id"], "status": res.get("status"),
                    "summary": res.get("summary"), "delta": res.get("delta")}
    raise ComputeError(
        "env.concurrent_extend",
        f"named env {name!r} kept moving under this extend (3 attempts) — "
        f"another agent/lane is extending it concurrently; retry when it settles",
        stage="aba")


# ── reclaim / retire ─────────────────────────────────────────────────────────

def evict(project_id: str, name: str, *, site: Optional[str] = None) -> dict:
    """Reclaim disk held by a named env's realizations (weft ``env_evict``), on a
    single `site` or every realized site. The env's IDENTITY and lock are kept —
    it rebuilds transparently from the lock on next use (see ``ensure_realized``).
    Returns ``{env_id, sites: {site: bytes_freed}, freed_bytes}``."""
    row = resolve(project_id, name)
    if row is None:
        raise ComputeError("unknown_env", f"no named env {name!r} in this project",
                           stage="aba", hints={"available": list_names(project_id)})
    env_id = row["env_id"]
    st = _sync(_adapter.get_compute().env_status(env_id))
    per_site: dict = {}
    for r in st.get("realizations", []):
        s = r.get("site")
        if site is not None and s != site:
            continue
        if r.get("state") != "ready":     # only a realized site holds disk to free
            continue
        # env_evict is a fast store op — the synchronous pass-through the module
        # reconciler uses (reconciler.py `_evict_pack_env`), not the async solve port.
        _adapter.get_compute().sync_call("env_evict", env_id, s)
        per_site[s] = int(r.get("bytes") or 0)
    return {"env_id": env_id, "sites": per_site,
            "freed_bytes": sum(per_site.values())}


def forget(project_id: str, name: str) -> dict:
    """Remove a named env's registry row — the name is gone from the project.
    REFUSED (no partial action) when it is the ACTIVE env; reset with
    set_active_env('default') first. Disk is not touched here (use ``evict``); a
    still-realized prefix is simply orphaned from the handle."""
    pid = str(project_id)
    row = resolve(pid, name)
    if row is None:
        raise ComputeError("unknown_env", f"no named env {name!r} in this project",
                           stage="aba", hints={"available": list_names(pid)})
    lang = row.get("language") or "python"
    if name == get_active(pid, lang):
        raise ComputeError(
            "active_env",
            f"'{name}' is the active {lang} env — call set_active_env('default') "
            f"before forgetting it", stage="aba")

    def _apply(data):
        data["envs"].pop(name, None)
        # defensively clear any active pointer still naming this env
        for _l, _a in list(data.get("active", {}).items()):
            if _a == name:
                data["active"][_l] = "default"
    _update(pid, _apply)
    return {"forgotten": name}


# ── realization / interpreter ────────────────────────────────────────────────

_LOCAL_SITE = "local"


def _local_site_root() -> Path:
    return _adapter.weft_workspace() / "site-local"


def _ready_prefix(env_id: str) -> Optional[Path]:
    """The realized prefix if it PHYSICALLY exists on the local site.

    The realization location is deterministic (`envs/<env-id-hash>` under the
    site root), and the physical activate.sh is the source of truth — weft's
    `env_status` `state` label can lag behind reality (after a repair+rebuild it
    may still read 'missing' while the prefix is materialized). So we check the
    deterministic path's activation directly, then any location weft lists, and
    trust the filesystem over the label."""
    root = _local_site_root()
    candidates = [root / "envs" / env_id.split(":")[-1]]
    try:
        st = _sync(_adapter.get_compute().env_status(env_id))
        for r in st.get("realizations", []):
            if r.get("site") == "local" and r.get("location"):
                candidates.append(root / r["location"])
    except Exception:  # noqa: BLE001 — the deterministic path still stands
        pass
    for loc in candidates:
        prefix = _prefix_from_activation(loc)
        if prefix is not None:
            return prefix
    return None


def _prefix_from_activation(location: Path) -> Optional[Path]:
    """The realized conda prefix, from weft's own activate.sh (CONDA_PREFIX=…) —
    strategy-agnostic, unlike hardcoding the pixi project layout."""
    act = location / "activate.sh"
    if not act.exists():
        return None
    m = re.search(r"^export CONDA_PREFIX=(.+)$", act.read_text(), re.MULTILINE)
    if not m:
        return None
    p = Path(m.group(1).strip().strip('"'))
    return p if p.exists() else None


# The realize task must actually EXERCISE the env's interpreter: a no-op `true`
# resolves from the system PATH and never touches the env, so a GC'd/evicted
# prefix is never rebuilt. A SIMPLE, single interpreter invocation forces
# realization. Language-specific because an R base pack has no `python`.
def _realize_probe(language: str) -> str:
    return "Rscript -e 'invisible()'" if language == "r" else "python -c pass"


def _run_realize_task(env_id: str, ad, timeout_s: int, language: str,
                      probe: Optional[str] = None, site: str = "local") -> str:
    """Submit an env-exercising task; return its terminal state. `probe` overrides
    the language default (e.g. a JVM CLI tool has neither python nor Rscript — it
    runs `<tool> --version`); the command MUST exercise the env or weft resolves
    it from the system PATH and skips materialization (the E1 finding).

    `force=True` is LOAD-BEARING: weft memoizes task results by (command, env,
    inputs) hash, so a repeated realize probe (same fixed command + EnvID) would
    hit the memo from the FIRST realization and return DONE *without* running —
    hence without rebuilding a since-evicted/GC'd prefix. force bypasses the
    memo so the task always runs, and weft's runner rebuilds a missing prefix
    from the lock as a side effect (found live: the eviction/repair path silently
    no-op'd for exactly this reason)."""
    sub = _sync(ad.task_submit({"command": probe or _realize_probe(language),
                                "env": env_id, "site": site,
                                "label": f"realize {env_id[:16]}"}, force=True))
    job_id = sub["job_id"]
    deadline = time.time() + timeout_s
    state, task_err = "PENDING", None
    while time.time() < deadline:
        row = _sync(ad.task_status(job_id))[0]
        state = row["state"]
        if state in ("DONE", "FAILED", "CANCELLED"):
            task_err = row.get("error")
            break
        time.sleep(1.0)
    return state, task_err


def ensure_tool_env(specs: list[str], *, name: str, probe: str,
                    eco: str = "conda", channels: Optional[list[str]] = None,
                    timeout_s: int = 1800) -> Path:
    """Provision a standalone CLI tool as a CONTENT-ADDRESSED weft env (not tied
    to any project — a shared, cached tool env, e.g. `nextflow`) and return its
    realized prefix; put `<prefix>/bin` on PATH to run the tool. This is the
    weft replacement for the old micromamba `TOOLS_ENV`. `probe` is a command
    that runs the tool INSIDE the env so weft materializes it (a no-op probe
    resolves from the host PATH and won't build — see `_run_realize_task`).
    `channels` adds solve channels (weft defaults to conda-forge; a bioconda
    tool like nextflow needs `["bioconda", "conda-forge"]`)."""
    spec: dict = {"name": name, "deps": {eco: list(specs)}}
    if channels:
        spec["channels"] = list(channels)
    res = _sync(_adapter.get_compute().env_ensure(spec))     # slow — cached
    return ensure_realized(res["env_id"], timeout_s=timeout_s, probe=probe)


def _realization_ready(env_id: str, site: str = "local") -> bool:
    """Strategy-BLIND readiness: is the env realized on the given site?

    True when either a materialized directory prefix exists (directory strategy,
    LOCAL only — a local prefix says nothing about a remote machine) OR weft's
    `env_status` reports a realization on that site with `state=="ready"` — the
    SQUASHFS case, where the env is a read-only image mounted only inside a weft
    task/kernel, so there is NO raw prefix on disk at rest. Use this to answer
    'is it built?' without demanding a path (kernel lanes hand the EnvID to weft,
    which mounts it; module reconcile just needs 'ready')."""
    if site == "local" and _ready_prefix(env_id) is not None:
        return True
    try:
        st = _sync(_adapter.get_compute().env_status(env_id))
    except Exception:  # noqa: BLE001
        return False
    return any(r.get("site") == site and r.get("state") == "ready"
               for r in st.get("realizations", []))


def realizations(env_id: str) -> list[dict]:
    """Every site's realization row for `env_id` — ``{site, state, bytes,
    idle_days}`` — from weft ``env_status``. The full list that
    ``_realization_ready`` reduces to a single-site bool; the env catalog and
    footprint surfaces need all of it. Raises on a substrate error (callers that
    render a catalog degrade per-env rather than fail)."""
    st = _sync(_adapter.get_compute().env_status(env_id))
    return [{"site": r.get("site"), "state": r.get("state"),
             "bytes": r.get("bytes"), "idle_days": r.get("idle_days")}
            for r in st.get("realizations", [])]


def ensure_ready(env_id: str, *, timeout_s: int = 900,
                 language: str = "python", probe: Optional[str] = None,
                 site: str = "local") -> None:
    """Realize the env ON THE GIVEN SITE if needed; return once it's READY.

    Strategy-blind — does NOT resolve a raw prefix (a squashfs env has none at
    rest). This is the correct call for consumers that only need the env BUILT and
    then run it THROUGH weft (interactive kernel lanes hand the EnvID to
    `kernel_start(env_id=…)` — which REFUSES an env not realized on its site,
    unlike task realize which builds implicitly; module reconcile just needs
    'ready'). Raw-prefix consumers (a subprocess exec of `<prefix>/bin/python`)
    use `ensure_realized`, which is valid only for the directory strategy."""
    if _realization_ready(env_id, site=site):
        return
    ad = _adapter.get_compute()
    state, task_err = _run_realize_task(env_id, ad, timeout_s, language,
                                        probe=probe, site=site)
    if not _realization_ready(env_id, site=site):
        # surface the realize TASK's own typed error (code + hints) when it
        # has one — a platform mismatch must arrive as env.platform_mismatch
        # so callers can lazy re-lock (job-lane parity; the generic
        # realize_failed wrapper used to swallow it and the kernel lane's
        # retry never fired — found live on the aarch64 slurm fixture)
        if isinstance(task_err, dict) and task_err.get("error"):
            raise ComputeError(
                str(task_err["error"]),
                f"{task_err.get('detail') or 'realize task failed'} "
                f"(realizing {env_id} on {site!r})",
                stage="realize", hints=task_err.get("hints") or {})
        raise ComputeError(
            "env.realize_failed",
            f"{env_id} could not be realized on {site!r} "
            f"(realize task state={state}) — its lock may be unbuildable there",
            stage="realize")


def ensure_realized(env_id: str, *, timeout_s: int = 900,
                    language: str = "python", probe: Optional[str] = None) -> Path:
    """The env's realized DIRECTORY prefix on the local site, realizing if needed.

    First use — or after the prefix is reclaimed (weft `env_evict`, GC, or a raw
    disk purge) — an env-exercising task (submitted with `force=True`, see
    `_run_realize_task`) materializes the env from its lock. weft's runner
    rebuilds a missing/demoted realization on its own; the force flag is what
    keeps the memo from short-circuiting that rebuild. `language` selects the
    probe interpreter (an R base pack has no python). Transparent to the agent
    (verified: a fully-deleted ~250 MB prefix rebuilds on the next run, for both
    the clean-eviction and raw-`rm -rf` reclaim paths).

    Returns a real on-disk prefix — valid ONLY for the directory realization
    strategy. When the env is realized but has no raw prefix (SQUASHFS: mounted
    only inside a weft task/kernel), this raises `env.no_raw_prefix` naming the
    fix: run the env through weft (`task_submit(env=…)` / `kernel_*`) instead of
    exec'ing a raw interpreter. Use `ensure_ready` when you don't need a path."""
    prefix = _ready_prefix(env_id)
    if prefix is not None:
        return prefix
    ad = _adapter.get_compute()
    state, _terr = _run_realize_task(env_id, ad, timeout_s, language,
                                     probe=probe)
    prefix = _ready_prefix(env_id)
    if prefix is None:
        if _realization_ready(env_id):
            raise ComputeError(
                "env.no_raw_prefix",
                f"{env_id} is realized (ready) but has no on-disk prefix — its "
                f"realization strategy (squashfs) is mounted only inside a weft "
                f"task/kernel. Run this env THROUGH weft (task_submit(env=…) or "
                f"kernel_start(env_id=…)) instead of resolving a raw interpreter.",
                stage="realize",
                hints={"env_id": env_id})
        raise ComputeError(
            "env.realize_failed",
            f"{env_id} could not be realized locally "
            f"(realize task state={state}) — its lock may be unbuildable here",
            stage="realize")
    return prefix


def interpreter(project_id: str, name: str) -> Path:
    """The named env's interpreter (python or Rscript by the env's language),
    realizing on first use."""
    row = resolve(project_id, name)
    if row is None:
        raise ComputeError("unknown_env", f"no named env {name!r} in this project",
                           stage="aba", hints={"available": list_names(project_id)})
    prefix = ensure_realized(row["env_id"], language=row["language"])
    exe = "Rscript" if row["language"] == "r" else "python"
    return prefix / "bin" / exe


# ── run / verify (one-shot, replaces iso.run_in / verify_imports-for-envs) ──

def run_in(project_id: str, name: str, code: str, *,
           timeout_s: int = 600, cwd: str | None = None) -> dict:
    """One-shot code run inside a named env. Returns {ok, stdout, stderr,
    returncode} (the retired iso.run_in contract).

    Strategy-blind: when the env has a real on-disk prefix (directory strategy),
    it runs the interpreter directly (fast path — exact rc, cwd). When it does NOT
    (SQUASHFS: mounted only inside a weft task/kernel), there is no raw
    `<prefix>/bin/python` to exec, so the run goes THROUGH weft — a task with
    `env=<EnvID>` that weft activates on the site (`run_in_via_weft`)."""
    row = resolve(project_id, name)
    if row is None:
        return {"ok": False, "stdout": "",
                "stderr": f"named env '{name}' does not exist in this project"}
    lang = row["language"]
    try:
        ensure_ready(row["env_id"], language=lang)   # realize; strategy-blind (no raise on squashfs)
    except ComputeError as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}
    prefix = _ready_prefix(row["env_id"])
    if prefix is None:
        # squashfs (no raw prefix at rest) → run through weft, which mounts+activates
        return _run_in_via_weft(row["env_id"], lang, code, timeout_s=timeout_s, cwd=cwd)
    exe = "Rscript" if lang == "r" else "python"
    interp = prefix / "bin" / exe
    suffix = ".R" if lang == "r" else ".py"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as f:
        f.write(code)
        script = f.name
    try:
        p = subprocess.run([str(interp), script], capture_output=True, text=True,
                           timeout=timeout_s, cwd=cwd)
        return {"ok": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr,
                "returncode": p.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"timed out ({timeout_s}s)"}
    finally:
        Path(script).unlink(missing_ok=True)


def _run_in_via_weft(env_id: str, language: str, code: str, *,
                     timeout_s: int = 600, cwd: str | None = None) -> dict:
    """Run a one-shot script inside `env_id` as a weft task on the local site —
    weft mounts+activates the env (squashfs-safe), so the interpreter resolves from
    the activated PATH with no raw prefix. Script + captured stdout/stderr live on
    the shared FS (readable after the task); the task's terminal state gives rc."""
    exe = "Rscript" if language == "r" else "python"
    suffix = ".R" if language == "r" else ".py"
    work = _adapter.weft_workspace().parent / "run_in" / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    script = work / f"code{suffix}"
    script.write_text(code)
    out, err = work / "stdout", work / "stderr"
    cd = f"cd {shlex.quote(cwd)} && " if cwd else ""
    cmd = (f"{cd}{exe} {shlex.quote(str(script))} "
           f"> {shlex.quote(str(out))} 2> {shlex.quote(str(err))}")
    ad = _adapter.get_compute()
    try:
        sub = _sync(ad.task_submit(
            {"command": cmd, "env": env_id, "site": _LOCAL_SITE,
             "label": f"run_in {env_id[:16]}"}, force=True))
        job_id = sub["job_id"]
        deadline = time.time() + timeout_s
        state = "PENDING"
        while time.time() < deadline:
            state = _sync(ad.task_status(job_id))[0]["state"]
            if state in ("DONE", "FAILED", "CANCELLED"):
                break
            time.sleep(0.5)
        stdout = out.read_text() if out.exists() else ""
        stderr = err.read_text() if err.exists() else ""
        if state not in ("DONE", "FAILED", "CANCELLED"):
            try:
                _sync(ad.task_cancel(job_id, why="run_in timeout"))
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "stdout": stdout,
                    "stderr": (stderr + f"\ntimed out ({timeout_s}s)").strip(),
                    "returncode": 124}
        # weft marks the task DONE only when the command exited 0; FAILED otherwise.
        return {"ok": state == "DONE", "stdout": stdout, "stderr": stderr,
                "returncode": 0 if state == "DONE" else 1}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify_imports(project_id: str, name: str, imports: list[str]) -> tuple[bool, str]:
    """Real import check inside the named env (the isolated-lane replacement for
    verify.verify_python_imports)."""
    if not imports:
        return True, ""
    code = "import importlib\n" + "\n".join(
        f"importlib.import_module({m!r})" for m in imports)
    r = run_in(project_id, name, code, timeout_s=120)
    return bool(r["ok"]), (r["stderr"] or "").strip()[-800:]


# ── platform-aware locking (misc/detached_compute.md) ────────────────────────

def controller_platform() -> str:
    """This controller's conda platform string (osx-arm64, linux-64, ...)."""
    import platform as _pl
    sysname = _pl.system().lower()
    mach = _pl.machine().lower()
    arch = {"x86_64": "64", "amd64": "64", "arm64": "arm64",
            "aarch64": "aarch64"}.get(mach, mach)
    if sysname == "darwin":
        return f"osx-{'arm64' if arch in ('arm64', 'aarch64') else '64'}"
    return f"linux-{arch}"


def ensure_platform(project_id: str, name: str, platform_str: str) -> dict:
    """Lazy re-lock at first remote use (detached lane): re-solve the named
    env for the target site's platform so weft can realize it THERE — from
    the row's PERSISTED base spec (python_version pin and all; reconstruction
    from the flat package list silently re-locked a 3.10-pinned env to the
    3.12 default), then re-apply each extend() layer as an extends_env link,
    mirroring how the env was actually built. Solve cost and platform-
    availability failures land on the remote attempt — local work is never
    blocked by other platforms."""
    row = resolve(project_id, name)
    if row is None:
        raise KeyError(f"no isolated env {name!r} in project {project_id}")
    language = row.get("language") or "python"
    plats = sorted({controller_platform(), platform_str,
                    *(row.get("platforms") or [])})
    # legacy rows (pre-persistence) still reconstruct — flat, but keeping any
    # recorded python pin
    base = dict(row.get("base_spec") or _spec_for(
        project_id, name, language, list(row.get("packages") or []),
        row.get("python_version"), list(row.get("conda_packages") or []) or None))
    base["platforms"] = plats
    res = _sync(_adapter.get_compute().env_ensure(base, update=True))
    env_id = res["env_id"]
    try:
        for layer in row.get("layers") or []:
            lspec = {"name": f"aba-{project_id}-{name}", "extends_env": env_id,
                     "deps": _layer_deps(layer, language), "platforms": plats}
            res = _sync(_adapter.get_compute().env_ensure(lspec))
            env_id = res["env_id"]
    except ComputeError as e:
        if getattr(e, "code", "") != "env.layer_conflict":
            raise
        # FLATTEN fallback (F-ENV-2, found live): replaying the extension
        # chain for a new platform re-solves each delta against a re-locked
        # parent — and a delta that needs base version moves conflicts the
        # same way it would have at extend time on that platform. The env's
        # cumulative CONTENT is what the user asked for, not the chain shape:
        # merge base deps + every layer into ONE spec and solve it fresh for
        # the target platforms. Same frozen-identity semantics (new EnvID).
        flat = {k: v for k, v in base.items() if k != "deps"}
        deps = {k: list(v) for k, v in (base.get("deps") or {}).items()}
        for layer in row.get("layers") or []:
            for k, v in _layer_deps(layer, language).items():   # eco-faithful merge
                cur = deps.setdefault(k, [])
                cur += [p for p in v if p not in cur]
        flat["deps"] = deps
        flat["platforms"] = plats
        res = _sync(_adapter.get_compute().env_ensure(flat, update=True))
        env_id = res["env_id"]
    now = time.time()
    _update(project_id, lambda data: data["envs"][name].update(
        {"env_id": env_id, "updated_at": now, "platforms": plats}))
    return {"env_id": env_id, "platforms": plats, "status": res.get("status")}
