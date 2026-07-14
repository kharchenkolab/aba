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

import asyncio
import json
import re
import subprocess
import tempfile
import time
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
    """Run a port coroutine from a worker thread. Loud on the loop thread —
    blocking the event loop on a conda solve is never acceptable."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("named_envs is sync-only: call from a worker thread, "
                       "not the event loop (use the async port directly there)")


# ── the per-project registry ─────────────────────────────────────────────────

def _registry_path(project_id: str) -> Path:
    from core.config import PROJECTS_DIR
    return PROJECTS_DIR / str(project_id) / "weft_envs.json"


def _load(project_id: str) -> dict:
    p = _registry_path(project_id)
    if not p.exists():
        return {"envs": {}, "active": {}}
    try:
        data = json.loads(p.read_text()) or {}
    except Exception:  # noqa: BLE001
        return {"envs": {}, "active": {}}
    data.setdefault("envs", {})
    data.setdefault("active", {})
    return data


def _save(project_id: str, data: dict) -> None:
    p = _registry_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1))


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
    data = _load(project_id)
    data["active"][lang] = name
    _save(project_id, data)
    return {"lang": lang, "active": name}


# ── create / extend ──────────────────────────────────────────────────────────

def _spec_for(project_id: str, name: str, language: str,
              packages: list[str]) -> dict:
    """A fresh named-env spec. Python envs bake ipykernel so the per-env
    persistent kernel works without ever installing into the frozen env; R envs
    get the cran layer for CRAN-style specs."""
    label = f"aba-{project_id}-{name}"
    if language == "r":
        conda = [p for p in packages if p.startswith(("r-", "bioconductor-"))]
        cran = [p for p in packages if not p.startswith(("r-", "bioconductor-"))]
        deps: dict = {"conda": ["r-base =4.4.*", *conda]}
        if cran:
            deps["cran"] = cran
        return {"name": label, "deps": deps}
    return {"name": label,
            "deps": {"conda": ["python =3.12", "ipykernel"],
                     "pypi": list(packages)}}


def create(project_id: str, name: str, *, language: str = "python",
           packages: list[str] | None = None) -> dict:
    """Solve a fresh named env → EnvID (realization is lazy — first run
    realizes). Raises ComputeError with weft's structured cause (e.g.
    env.solve_conflict names the minimal conflicting set)."""
    name = name.strip()
    spec = _spec_for(project_id, name, language, packages or [])
    res = _sync(_adapter.get_compute().env_ensure(spec))
    data = _load(project_id)
    now = time.time()
    data["envs"][name] = {
        "env_id": res["env_id"], "language": language,
        "packages": list(packages or []), "history": [],
        "created_at": now, "updated_at": now,
    }
    _save(project_id, data)
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
    res = _sync(_adapter.get_compute().env_ensure(spec))
    data = _load(project_id)
    row = data["envs"][name]
    row["history"].append(row["env_id"])
    row["env_id"] = res["env_id"]
    row["packages"] = list(dict.fromkeys([*row["packages"], *packages]))
    row["updated_at"] = time.time()
    _save(project_id, data)
    return {"env_id": res["env_id"], "status": res.get("status"),
            "summary": res.get("summary"), "delta": res.get("delta")}


# ── realization / interpreter ────────────────────────────────────────────────

def _local_site_root() -> Path:
    return _adapter.weft_workspace() / "site-local"


def _ready_prefix(env_id: str) -> Optional[Path]:
    st = _sync(_adapter.get_compute().env_status(env_id))
    for r in st.get("realizations", []):
        if r.get("site") == "local" and r.get("state") == "ready":
            loc = _local_site_root() / r["location"]
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


def ensure_realized(env_id: str, *, timeout_s: int = 900) -> Path:
    """The env's realized prefix on the local site, realizing it if needed (a
    no-op weft task triggers realization; weft rebuilds a GC-reclaimed env from
    its lock here transparently — the old §11.6 story, now weft's job)."""
    prefix = _ready_prefix(env_id)
    if prefix is not None:
        return prefix
    ad = _adapter.get_compute()
    sub = _sync(ad.task_submit({"command": "true", "env": env_id, "site": "local",
                                "label": f"realize {env_id[:16]}"}))
    job_id = sub["job_id"]
    deadline = time.time() + timeout_s
    state = "PENDING"
    while time.time() < deadline:
        state = _sync(ad.task_status(job_id))[0]["state"]
        if state in ("DONE", "FAILED", "CANCELLED"):
            break
        time.sleep(1.0)
    if state != "DONE":
        detail = f"realization job {job_id} state={state}"
        if state == "FAILED":
            res = _sync(ad.task_result(job_id))
            detail += f": {str(res.get('logs', {}).get('tail'))[-400:]}"
        raise ComputeError("env.realize_failed", detail, stage="realize",
                           retryable=state not in ("FAILED",))
    prefix = _ready_prefix(env_id)
    if prefix is None:
        raise ComputeError("env.realize_failed",
                           f"{env_id} ran but no ready local realization found",
                           stage="realize")
    return prefix


def interpreter(project_id: str, name: str) -> Path:
    """The named env's interpreter (python or Rscript by the env's language),
    realizing on first use."""
    row = resolve(project_id, name)
    if row is None:
        raise ComputeError("unknown_env", f"no named env {name!r} in this project",
                           stage="aba", hints={"available": list_names(project_id)})
    prefix = ensure_realized(row["env_id"])
    exe = "Rscript" if row["language"] == "r" else "python"
    return prefix / "bin" / exe


# ── run / verify (one-shot, replaces iso.run_in / verify_imports-for-envs) ──

def run_in(project_id: str, name: str, code: str, *,
           timeout_s: int = 600, cwd: str | None = None) -> dict:
    """One-shot code run inside a named env. Returns {ok, stdout, stderr,
    returncode} (the retired iso.run_in contract)."""
    row = resolve(project_id, name)
    if row is None:
        return {"ok": False, "stdout": "",
                "stderr": f"named env '{name}' does not exist in this project"}
    try:
        interp = interpreter(project_id, name)
    except ComputeError as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}
    suffix = ".R" if row["language"] == "r" else ".py"
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


def verify_imports(project_id: str, name: str, imports: list[str]) -> tuple[bool, str]:
    """Real import check inside the named env (the isolated-lane replacement for
    env_integrity.verify_python_imports)."""
    if not imports:
        return True, ""
    code = "import importlib\n" + "\n".join(
        f"importlib.import_module({m!r})" for m in imports)
    r = run_in(project_id, name, code, timeout_s=120)
    return bool(r["ok"]), (r["stderr"] or "").strip()[-800:]
