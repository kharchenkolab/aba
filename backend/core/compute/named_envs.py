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
    _update(project_id, lambda data: data["active"].__setitem__(lang, name))
    return {"lang": lang, "active": name}


# ── create / extend ──────────────────────────────────────────────────────────

def _spec_for(project_id: str, name: str, language: str,
              packages: list[str], python_version: Optional[str] = None) -> dict:
    """A fresh named-env spec. Python envs bake ipykernel so the per-env
    persistent kernel works without ever installing into the frozen env; R envs
    get the cran layer for CRAN-style specs. `python_version` (e.g. "3.10")
    pins the interpreter — the whole point of an isolated env is often a
    DIFFERENT python than the base (an old package that needs <3.11); a fresh
    env has no frozen base to conflict, so weft picks the matching build."""
    label = f"aba-{project_id}-{name}"
    if language == "r":
        conda = [p for p in packages if p.startswith(("r-", "bioconductor-"))]
        cran = [p for p in packages if not p.startswith(("r-", "bioconductor-"))]
        deps: dict = {"conda": ["r-base =4.4.*", *conda]}
        if cran:
            deps["cran"] = cran
        return {"name": label, "deps": deps}
    pyspec = f"python ={python_version}" if python_version else "python =3.12"
    return {"name": label,
            "deps": {"conda": [pyspec, "ipykernel"],
                     "pypi": list(packages)}}


def create(project_id: str, name: str, *, language: str = "python",
           packages: list[str] | None = None,
           python_version: Optional[str] = None) -> dict:
    """Solve a fresh named env → EnvID (realization is lazy — first run
    realizes). Raises ComputeError with weft's structured cause (e.g.
    env.solve_conflict names the minimal conflicting set)."""
    name = name.strip()
    spec = _spec_for(project_id, name, language, packages or [], python_version)
    res = _sync(_adapter.get_compute().env_ensure(spec))     # slow — OUTSIDE the lock
    now = time.time()
    _update(project_id, lambda data: data["envs"].__setitem__(name, {
        "env_id": res["env_id"], "language": language,
        "packages": list(packages or []), "history": [],
        "created_at": now, "updated_at": now,
    }))
    return {"env_id": res["env_id"], "status": res.get("status"),
            "summary": res.get("summary"), "engine": "weft"}


def extend(project_id: str, name: str, packages: list[str]) -> dict:
    """Add packages = extends_env over the current EnvID → NEW EnvID, handle
    moves, old id kept in history (never install into a frozen env)."""
    row = resolve(project_id, name)
    if row is None:
        raise ComputeError("unknown_env", f"no named env {name!r} in this project",
                           stage="aba", hints={"available": list_names(project_id)})
    eco = "cran" if row["language"] == "r" else "pypi"
    spec = {"name": f"aba-{project_id}-{name}",
            "extends_env": row["env_id"], "deps": {eco: list(packages)}}
    res = _sync(_adapter.get_compute().env_ensure(spec))     # slow — OUTSIDE the lock

    def _apply(data):
        r = data["envs"].get(name)
        if r is None:      # vanished concurrently — re-seed from the solved id
            r = {"env_id": row["env_id"], "language": row["language"],
                 "packages": list(row["packages"]), "history": []}
            data["envs"][name] = r
        r.setdefault("history", []).append(r["env_id"])
        r["env_id"] = res["env_id"]
        r["packages"] = list(dict.fromkeys([*r.get("packages", []), *packages]))
        r["updated_at"] = time.time()
    _update(project_id, _apply)
    return {"env_id": res["env_id"], "status": res.get("status"),
            "summary": res.get("summary"), "delta": res.get("delta")}


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
                      probe: Optional[str] = None) -> str:
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
                                "env": env_id, "site": "local",
                                "label": f"realize {env_id[:16]}"}, force=True))
    job_id = sub["job_id"]
    deadline = time.time() + timeout_s
    state = "PENDING"
    while time.time() < deadline:
        state = _sync(ad.task_status(job_id))[0]["state"]
        if state in ("DONE", "FAILED", "CANCELLED"):
            break
        time.sleep(1.0)
    return state


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


def _realization_ready(env_id: str) -> bool:
    """Strategy-BLIND readiness: is the env realized on the local site?

    True when either a materialized directory prefix exists (directory strategy)
    OR weft's `env_status` reports a local realization with `state=="ready"` — the
    SQUASHFS case, where the env is a read-only image mounted only inside a weft
    task/kernel, so there is NO raw prefix on disk at rest. Use this to answer
    'is it built?' without demanding a path (kernel lanes hand the EnvID to weft,
    which mounts it; module reconcile just needs 'ready')."""
    if _ready_prefix(env_id) is not None:
        return True
    try:
        st = _sync(_adapter.get_compute().env_status(env_id))
    except Exception:  # noqa: BLE001
        return False
    return any(r.get("site") == "local" and r.get("state") == "ready"
               for r in st.get("realizations", []))


def ensure_ready(env_id: str, *, timeout_s: int = 900,
                 language: str = "python", probe: Optional[str] = None) -> None:
    """Realize the env on the local site if needed; return once it's READY.

    Strategy-blind — does NOT resolve a raw prefix (a squashfs env has none at
    rest). This is the correct call for consumers that only need the env BUILT and
    then run it THROUGH weft (interactive kernel lanes hand the EnvID to
    `kernel_start(env_id=…)`; module reconcile just needs 'ready'). Raw-prefix
    consumers (a subprocess exec of `<prefix>/bin/python`) use `ensure_realized`,
    which is valid only for the directory strategy."""
    if _realization_ready(env_id):
        return
    ad = _adapter.get_compute()
    state = _run_realize_task(env_id, ad, timeout_s, language, probe=probe)
    if not _realization_ready(env_id):
        raise ComputeError(
            "env.realize_failed",
            f"{env_id} could not be realized locally "
            f"(realize task state={state}) — its lock may be unbuildable here",
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
    state = _run_realize_task(env_id, ad, timeout_s, language, probe=probe)
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
