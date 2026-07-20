"""Bio discovery cluster — search-the-world / fetch-from-the-world
/ install-or-propose tool impls (WU-3-tail).

Pure search tools (search_skills_tool, search_bioconda, search_nf_core,
search_mcp_registry) + capability ops (inspect_package, ensure_capability,
propose_capability_tool) + external fetches (fetch_url, fetch_ensembl,
lookup_sra_runinfo). Includes the HTTP-GET-JSON helper, MCP registry
URL constant, and import-name detection helpers used by ensure_capability."""

from __future__ import annotations
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from core import config

# Cross-cluster helpers — inspect_package dispatches to either run_python
# or run_r (with the language-specific introspection snippet) depending
# on the requested language. Both run functions live in run_exec.py;
# the snippets live in ctx_read.py (alongside the other read tools).
from .run_exec import run_python, run_r
from .ctx_read import _py_inspect_code, _r_inspect_code


# Indirection so tests can stub the network without monkeypatching urllib.
_HTTP_GET_JSON = None  # late-bound below to _http_get_json
_DEFAULT_MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


def inspect_package(input_: dict, ctx: dict | None = None) -> dict:
    """One-call orientation for an unfamiliar library: exported symbols,
    signatures (Python) / vignettes + R6 methods (R), and optional focus on one
    function/class — so the agent learns the real API instead of trial-and-error.
    The package must already be importable (ensure_capability first)."""
    name = (input_.get("name") or "").strip()
    if not name:
        return {"status": "error", "note": "inspect_package needs a `name`."}
    lang = (input_.get("language") or "python").strip().lower()
    focus = input_.get("object") or input_.get("function") or input_.get("focus")
    if lang in ("r", "rlang", "R"):
        res = run_r({"code": _r_inspect_code(name, focus), "timeout_s": 120}, ctx)
        lang = "r"
    else:
        res = run_python({"code": _py_inspect_code(name, focus), "fresh": True, "timeout_s": 120}, ctx)
        lang = "python"
    report = (res.get("stdout") or "").strip()
    err = (res.get("stderr") or res.get("error") or "").strip()
    if (not report or "IMPORT_ERROR" in report or "LOAD_ERROR" in report) and (err or "ERROR" in report):
        return {"status": "error", "name": name, "language": lang,
                "note": f"Couldn't introspect {name!r} — is it installed? "
                        f"ensure_capability first. ({(report or err)[-300:]})"}
    return {"status": "ok", "name": name, "language": lang, "report": report[:4000]}


def inspect_env(input_: dict, ctx: dict | None = None) -> dict:
    """Diagnose the runtime ENVIRONMENT for troubleshooting (the agent's read
    layer for env problems). Differs from inspect_package (which learns an
    *importable* package's API): this tells you whether/why something loads.

    With `name`: does it load (real import / library()), its version, WHICH tier
    owns it (base | shared-overlay | project-overlay/-lib), and the error if it's
    present-but-broken (ABI mismatch / partial install — the tensorflow case).
    Without `name`: an overview of the tiers + base-lock state. `language` =
    python (default) | r."""
    name = (input_.get("name") or "").strip()
    lang = (input_.get("language") or "python").strip().lower()
    is_r = lang in ("r", "rlang")
    from core import projects
    pid = projects.current()

    if not name:
        from core.exec.env_integrity import env_overview
        ov = env_overview(pid)
        if is_r:
            # The weft R session prefix is the R env (no project-lib overlay).
            from core.compute import base_env as _bev, project_env as _penv
            try:
                ov["r_session"] = (str(_penv.prefix(str(pid), "r"))
                                   if (pid and _bev.active("r")) else None)
            except Exception:  # noqa: BLE001 — overview must not fail on R
                ov["r_session"] = None
        # The project's named-env catalog — what exists, what's in each, where it
        # is realized and how much disk it holds. env_status is per-env, but this
        # tool is on-demand (unlike the per-turn compute line), so substrate calls
        # are fine; a per-env substrate error degrades to "unavailable", never fails.
        from core.compute import named_envs as _ne
        try:
            _names = _ne.list_names(pid) if pid else []
        except Exception:  # noqa: BLE001 — no registry / unreadable → empty catalog
            _names = []
        _active = {"python": _ne.get_active(pid, "python") if pid else "default",
                   "r": _ne.get_active(pid, "r") if pid else "default"}
        catalog = []
        for _n in _names:
            _row = _ne.resolve(pid, _n) or {}
            _lang = _row.get("language") or "python"
            try:
                _reals = _ne.realizations(_row["env_id"])
            except Exception:  # noqa: BLE001 — substrate hiccup on ONE env only
                _reals = "unavailable"
            catalog.append({
                "name": _n, "language": _lang,
                "packages": list(_row.get("packages") or []),
                "active": (_n == _active.get(_lang)),
                "env_id": _row.get("env_id"),
                "created_at": _row.get("created_at"),
                "realizations": _reals,
            })
        return {"status": "ok", "scope": "overview", "language": "r" if is_r else "python",
                "tiers": ov, "named_envs": catalog}

    if is_r:
        # Probe the project's weft R SESSION (base pack + additions); its own
        # .libPaths() is authoritative, so there is no project-lib overlay to
        # stack. requireNamespace = real load; packageVersion + find.package give
        # version/location. No R pack declared → the R lane is unavailable here.
        import subprocess
        from core.compute import base_env as _bev, project_env as _penv
        from core.compute.errors import ComputeError
        if not _bev.active("r"):
            return {"status": "unavailable", "name": name, "language": "r",
                    "loads": False,
                    "error": "no R environment pack is declared for this deployment"}
        try:
            _rs = str(_penv.interpreter(str(pid or "_none"), "r"))
        except (ComputeError, RuntimeError) as e:
            return {"status": "error", "name": name, "language": "r",
                    "loads": False, "error": f"R session unavailable: {e}"}
        expr = (f"ok <- requireNamespace({name!r}, quietly=TRUE); "
                + f"cat('ABA_LOADS=', isTRUE(ok), '\\n', sep=''); "
                + f"if (isTRUE(ok)) {{ cat('ABA_VER=', as.character(packageVersion({name!r})), '\\n', sep=''); "
                + f"cat('ABA_LOC=', find.package({name!r}), '\\n', sep='') }}")
        try:
            proc = subprocess.run([_rs, "-e", expr], capture_output=True,
                                  text=True, timeout=120)
            out, err = proc.stdout or "", proc.stderr or ""
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": name, "language": "r",
                    "loads": False, "error": str(e)[:400]}

        def _pick(key):
            for ln in out.splitlines():
                if ln.startswith(key):
                    return ln[len(key):].strip()
            return None
        loads = (_pick("ABA_LOADS=") == "TRUE")
        return {"status": "ok", "name": name, "language": "r", "loads": loads,
                "version": _pick("ABA_VER="), "location": _pick("ABA_LOC="),
                "tier": ("session" if loads else "unknown"),
                "error": None if loads else (err or out)[-600:]}

    from core.exec.env_integrity import python_package_status
    st = python_package_status(name, project_id=pid)
    return {"status": "ok", "language": "python", **st}


def make_isolated_env(input_: dict, ctx: dict | None = None) -> dict:
    """Create/refresh an ISOLATED environment you OWN (a weft-solved env — Python,
    or with language='r' a standalone R env) with FULL version control. USE THIS
    when a package conflicts with the base (a different numpy, tensorflow, an
    ABI-incompatible wheel) or you need to resolve a dependency conflict your own
    way — the shared base is never touched. Run code in it with
    run_in_isolated_env. Returns {status, name, language, engine, env_id,
    installed, verified, error}."""
    from core.compute import named_envs
    from core.compute.errors import ComputeError
    from core import projects
    name = (input_.get("name") or "").strip()
    if not name:
        return {"status": "error", "note": "make_isolated_env needs a `name`."}
    if named_envs.is_reserved_name(name):
        return {"status": "error", "name": name,
                "note": f"'{name}' is reserved (default/base/shared/project) — it denotes "
                        "the normal environment, not an isolated one. Pick another name."}
    is_r = (input_.get("language") or "python").strip().lower() in ("r", "rlang")
    label = "R" if is_r else "Python"
    lang = "r" if is_r else "python"
    packages = list(input_.get("packages") or [])
    pid = str(projects.current() or "default")
    try:
        # Existing env + packages → layer on (extends_env; the env is never
        # mutated in place). Fresh name → solve a new env. Solving is eager so
        # conflicts surface NOW with weft's structured cause; realization is
        # lazy — the first run materializes the prefix.
        if named_envs.resolve(pid, name) is not None and packages:
            res = named_envs.extend(pid, name, packages)
            # Extension mints a NEW frozen EnvID — a kernel already running on
            # the old realization would never see the new packages (found live:
            # in-session imports kept failing after a successful extend). Shut
            # the env's live sessions down; the next step re-attaches to the
            # new identity. In-kernel state is gone by design — say so below.
            _restarted = _evict_env_kernels(name)
        else:
            res = named_envs.create(pid, name, language=lang, packages=packages,
                                    python_version=(input_.get("python_version") or None))
            _restarted = 0
    except ComputeError as e:
        return {"status": "error", "name": name, "language": lang,
                "error": e.to_payload(),
                "note": f"could not solve the env: {e.detail or e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "name": name, "note": f"could not create env: {e}"}
    out = {"status": "ok", "name": name, "language": lang, "engine": "weft",
           "env_id": res["env_id"], "installed": packages}
    verify = input_.get("verify_imports")
    if packages and verify:
        ok, err = named_envs.verify_imports(pid, name, list(verify))
        out["verified"] = ok
        if not ok:
            return {**out, "status": "error", "error": err,
                    "note": "Env solved, but the requested imports failed inside it — see error."}
    _run = "run_r" if is_r else "run_python"
    out["note"] = (f"Isolated {label} env {name!r} solved; run code in it with "
                   f"{_run}(env={name!r}, code=…) — the first run materializes it. "
                   f"Calling make_isolated_env again with more packages LAYERS them on. "
                   f"Listed in inspect_env(); survives across threads.")
    if _restarted:
        out["note"] += (f" NOTE: the env's running session was restarted to pick "
                        f"up the new packages — in-memory objects from earlier "
                        f"steps in this env are gone; reload what you need.")
    return out


def _evict_env_kernels(env_name: str) -> int:
    """Shut down live kernel sessions attached to a named env (identity change
    or disk evict). Best-effort — a pool failure must not fail the env op."""
    try:
        from core.exec.kernels import get_pool
        return get_pool().evict_env_sessions(env_name)
    except Exception:  # noqa: BLE001
        return 0


def run_in_isolated_env(input_: dict, ctx: dict | None = None) -> dict:
    """Run code inside an isolated env created by make_isolated_env — your sandbox
    for conflict resolution / troubleshooting. `language` = python (default) | r.
    Returns {status, language, stdout, stderr}."""
    from core.compute import named_envs
    from core import projects
    name = (input_.get("name") or "").strip()
    code = input_.get("code") or ""
    if not name or not code:
        return {"status": "error", "note": "run_in_isolated_env needs `name` and `code`."}
    is_r = (input_.get("language") or "python").strip().lower() in ("r", "rlang")
    # same ceiling as every other exec lane (run_python/run_r/remote-sync) —
    # this tool alone accepted an unbounded ask (limits-parity review)
    ts = max(5, min(int(input_.get("timeout_s") or 600), 1800))
    pid = str(projects.current() or "default")
    if named_envs.resolve(pid, name) is None:
        return {"status": "error", "name": name,
                "note": f"No isolated env '{name}'. Create it with make_isolated_env("
                        f"name='{name}'" + (", language='r'" if is_r else "") + ")."}
    r = named_envs.run_in(pid, name, code, timeout_s=ts)
    return {"status": "ok" if r["ok"] else "error", "name": name,
            "language": "r" if is_r else "python", "stdout": r["stdout"], "stderr": r["stderr"]}


def set_active_env(input_: dict, ctx: dict | None = None) -> dict:
    """§11.2 — set the project's ACTIVE python env; bare run_python uses it until
    changed. name='default' resets to the normal served stack. (Python only — R's
    per-project lib already overrides the base, so run_r has no active pointer.)"""
    from core.compute import named_envs
    from core import projects
    name = (input_.get("name") or "").strip()
    if not name:
        return {"status": "error", "note": "set_active_env needs a `name` (or 'default')."}
    pid = str(projects.current() or "default")
    if name.lower() != "default" and named_envs.resolve(pid, name) is None:
        return {"status": "error", "name": name,
                "note": f"No isolated python env '{name}'. Create it with make_isolated_env, "
                        "or pass 'default' to use the normal environment."}
    named_envs.set_active(pid, name, "python")
    if name.lower() == "default":
        return {"status": "ok", "active_python_env": "default",
                "note": "Bare run_python now uses the default served stack."}
    return {"status": "ok", "active_python_env": name,
            "note": f"Bare run_python now runs in '{name}'. Use env='default' for a one-off "
                    f"in the normal stack, or set_active_env('default') to switch back."}


def evict_env(input_: dict, ctx: dict | None = None) -> dict:
    """Reclaim the disk a named env's realizations hold on a machine — or retire
    the env entirely. Wraps weft's evict over the env's realizations: eviction
    keeps the env's identity + lock, so it rebuilds transparently on next use;
    `forget=True` additionally removes the project's registry row."""
    from core.compute import named_envs
    from core.compute.errors import ComputeError
    from core import projects
    name = (input_.get("name") or "").strip()
    if not name:
        return {"status": "error", "note": "evict_env needs a `name`."}
    site = (input_.get("site") or "").strip() or None
    forget = bool(input_.get("forget"))
    pid = str(projects.current() or "default")
    if named_envs.resolve(pid, name) is None:
        return {"status": "error", "name": name,
                "note": f"No named env '{name}' in this project. Call inspect_env() to see "
                        f"the project's named-env catalog."}
    # forget=True is evict-and-forget in one call — but a still-active env is
    # refused BEFORE any eviction, so there is no partial action.
    if forget and name == named_envs.get_active(pid,
                            (named_envs.resolve(pid, name) or {}).get("language") or "python"):
        return {"status": "error", "name": name,
                "note": f"'{name}' is the active env — call set_active_env('default') before "
                        f"forgetting it (no disk was evicted)."}
    # A live kernel session holds its realization's prefix open — shut the
    # env's sessions down BEFORE evicting the bytes underneath them.
    _evict_env_kernels(name)
    try:
        freed = named_envs.evict(pid, name, site=site)
    except ComputeError as e:
        return {"status": "error", "name": name,
                "note": f"could not evict '{name}': {e.detail or e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "name": name, "note": f"could not evict '{name}': {e}"}
    scope = f"site '{site}'" if site else "all sites"
    out = {"status": "ok", "name": name, "site": site,
           "sites": freed["sites"], "freed_bytes": freed["freed_bytes"],
           "note": (f"Evicted {name!r} on {scope}: {freed['freed_bytes']} bytes freed. "
                    f"The env rebuilds from its lock on next use — nothing is lost but time.")}
    if forget:
        try:
            named_envs.forget(pid, name)
        except ComputeError as e:
            return {**out, "status": "error",
                    "note": out["note"] + f" BUT forget failed: {e.detail or e.code}"}
        out["forgotten"] = True
        out["note"] += f" '{name}' is now gone from the project's named-env registry."
    return out


def _is_constraint_conflict(msg: str) -> bool:
    """Does an install failure look like UNSAT-against-the-base (a version/
    constraint conflict the pinned base forbids), vs a transient/typo/network
    error? Conservative — only clear resolver-conflict signals, so we never
    mis-route a fat-fingered package name into isolation. Covers BOTH the pip
    resolver strings (served-base lane) AND weft's structured session/layer
    conflict signals (W3.4 pack lane — a session_install/extends_env delta that
    contradicts the frozen base), so a conflicting capability auto-isolates in
    either deployment instead of hard-erroring."""
    m = (msg or "").lower()
    return any(s in m for s in (
        "resolutionimpossible",
        "conflicting dependencies",
        "the conflict is caused by",
        # weft (ComputeError.__str__ carries the code + detail):
        "env.solve_conflict",
        "env.layer_conflict",
        "incremental install failed in session",
        "unsatisfiable as pinned",
    ))


def _auto_isolate(name: str, pip_specs: list[str], cap: dict) -> dict:
    """UNSAT against the base → solve an ISOLATED weft env the agent owns
    (base untouched). The capability is NOT importable in run_python; the agent
    runs its code via run_in_isolated_env."""
    from core.compute import named_envs
    from core.compute.errors import ComputeError
    from core import projects
    env_name = f"cap-{name}"
    imp = cap.get("import_name")
    pid = str(projects.current() or "default")
    try:
        res = named_envs.create(pid, env_name, language="python", packages=pip_specs)
    except ComputeError as ce:
        return {"status": "error", "name": name, "isolated_env": env_name,
                "note": "conflicts with the base AND the isolated solve also failed — see error.",
                "error": ce.to_payload()}
    except Exception as ie:  # noqa: BLE001
        return {"status": "error", "name": name,
                "note": f"conflicts with the base, and the isolated-env fallback failed: {ie}"}
    verified = None
    if imp:
        ok, err = named_envs.verify_imports(pid, env_name, [imp])
        verified = ok
        if not ok:
            return {"status": "error", "name": name, "isolated_env": env_name,
                    "note": "conflicts with the base AND the isolated install also failed — see error.",
                    "error": err}
    return {"status": "ready_isolated", "name": name, "isolated_env": env_name,
            "env_id": res["env_id"], "installed": pip_specs, "verified": verified,
            "note": (f"{name} conflicts with the base environment, so it was installed in an "
                     f"ISOLATED env {env_name!r} (the shared base was left untouched). It is NOT "
                     f"importable in run_python — run its code with "
                     f"run_in_isolated_env(name={env_name!r}, code=...).")}


def search_skills_tool(input_: dict) -> dict:
    """Intent search over the skill (recipe) library. The system prompt only
    surfaces a relevant slice of skills; this finds the rest by free-text
    intent ('differential expression', 'cluster single cell data') so the
    agent isn't limited to what happened to be in-prompt this turn. Pass
    `domain` to narrow to one facet (see the domain map in the skills index)."""
    from core.skills import search_skills
    from core.skills.loader import unmet_tools
    q = (input_.get("query") or "").strip()
    if not q:
        return {"status": "error", "note": "search_skills needs a non-empty `query`."}
    limit = input_.get("limit") or 8
    hits = search_skills(q, limit=int(limit), domain=input_.get("domain"))
    skills, any_blocked = [], False
    any_knowhow = any(getattr(s, "kind", "recipe") == "knowhow" for s in hits)
    for s in hits:
        unmet = unmet_tools(s)          # tools this recipe needs that can't run here
        if unmet:
            any_blocked = True
        skills.append({
            # `invoke_with` is the literal tool call the agent should make next.
            # First so it leads on serialization — for a pattern-matching model
            # the first visible field shapes its next action.
            "invoke_with": f'Skill(skill="{s.name}")',
            "name":        s.name,
            "kind":        s.kind,        # 'recipe' (executable) | 'knowhow' (decision guide)
            "description": s.description,
            "when_to_use": s.when_to_use,
            "domain":      s.domain,
            "capabilities_needed": list(s.capabilities_needed),
            "runnable_here": not unmet,
            "unmet_tools": unmet,
        })
    note = ("Each result is a SKILL — invoke it via its `invoke_with` value "
            "(which calls the `Skill` tool). The `name` alone is NOT a callable tool.")
    if any_knowhow:
        note += (" Results are typed by `kind`: `recipe` = a runnable procedure; "
                 "`knowhow` = a decision guide (which method / why — not a runnable "
                 "workflow). For a specific execution request, bind the recipe; for an "
                 "open method-choice question, read the knowhow first; when both appear, "
                 "the knowhow explains the choice that the recipe executes.")
    if any_blocked:
        note += (" Results with `runnable_here: false` describe the right approach but "
                 "need a tool that can't run in this environment (see `unmet_tools`, "
                 "e.g. `run_nextflow` → run on HPC / a cluster) — explain that rather "
                 "than launching them here.")
    return {"skills": skills, "note": note}


def search_bioconda(input_: dict) -> dict:
    """Check whether a tool exists on bioconda AND whether a cluster environment
    module provides it. On a cluster the module is preferred (faster than a conda
    build, and covers tools NOT on bioconda — e.g. cellranger), so it's surfaced
    here where the agent looks for tool availability."""
    import json as _json
    import urllib.error
    import urllib.request

    name = (input_.get("query") or input_.get("name") or "").strip().lower()
    if not name:
        return {"error": "query is required"}
    # Cluster module match (exact name; a no-op off a cluster).
    _mod = None
    try:
        from core.exec import modules as _modprov
        if _modprov.modules_active():
            _mod = _modprov.resolve(name)
    except Exception:  # noqa: BLE001
        _mod = None
    try:
        with urllib.request.urlopen(
            f"https://api.anaconda.org/package/bioconda/{name}", timeout=10
        ) as resp:
            data = _json.loads(resp.read())
        result = {
            "found": True, "name": name,
            "latest_version": data.get("latest_version"),
            "summary": data.get("summary"),
            "note": "Available on bioconda and installable on demand: call "
                    "propose_capability(name, archetype='cli') then ensure_capability — "
                    "it installs into the conda tools env and lands on PATH for run_python.",
        }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            result = {"found": False, "name": name}
        else:
            result = {"error": f"bioconda lookup failed ({e.code})"}
    except Exception as e:  # noqa: BLE001
        result = {"error": f"bioconda lookup failed: {e}"}
    if _mod:
        result["found"] = True
        result["cluster_module"] = _mod
        result["note"] = (
            f"Provided by cluster environment module '{_mod}' — call "
            f"ensure_capability('{name}') to use it (loaded for run_python and for "
            f"background Slurm jobs; preferred over a conda build). "
            + result.get("note", ""))
    return result


def _http_get_json(url: str, timeout: int = 15) -> dict:
    """GET a URL and parse JSON. Browser UA (some hosts 403 bare urllib).
    Raises on network/parse error — callers translate to a graceful note."""
    import json as _json
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ABA discovery)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read())


# Bind the late-bound alias (above) now that _http_get_json is defined.
_HTTP_GET_JSON = _http_get_json


def search_nf_core(input_: dict) -> dict:
    """Discover nf-core pipelines by intent (item 3). Fetches the public
    nf-co.re pipelines index and ranks it with our BM25 over name +
    description + topics. A discovered pipeline can be catalogued via
    propose_capability(archetype='pipeline'); actually running it needs a
    Nextflow runtime (not yet wired), so adoption is record-only for now."""
    q = (input_.get("query") or "").strip()
    if not q:
        return {"status": "error", "note": "search_nf_core needs a non-empty `query`."}
    limit = int(input_.get("limit") or 8)
    try:
        data = _HTTP_GET_JSON("https://nf-co.re/pipelines.json")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "note": f"Could not reach nf-core registry: {e}"}
    pipelines = data.get("remote_workflows") or data.get("pipelines") or []
    from core.search import BM25
    by_name: dict[str, dict] = {}
    docs = []
    for p in pipelines:
        name = p.get("name") or ""
        if not name:
            continue
        topics = " ".join(p.get("topics") or [])
        by_name[name] = p
        docs.append((name, f"{name} {p.get('description','')} {topics}"))
    ranked = [n for n, _ in BM25(docs).search(q, limit=limit)]
    out = []
    for name in ranked:
        p = by_name[name]
        rels = p.get("releases") or []
        latest = rels[0].get("tag_name") if rels and isinstance(rels[0], dict) else None
        out.append({
            "name": name,
            "description": p.get("description"),
            "topics": p.get("topics") or [],
            "url": f"https://nf-co.re/{name}",
            "latest_release": latest,
        })
    return {"pipelines": out, "total_indexed": len(docs),
            "note": "Adopt one with propose_capability(name, archetype='pipeline'). "
                    "Running pipelines needs a Nextflow runtime (deferred)."}


def _mcp_registry_url() -> str:
    """Public MCP server registry. Override via ABA_MCP_REGISTRY_URL to point at
    Smithery / an internal registry without code changes (read at call time)."""
    return config.settings.mcp_registry_url.get() or _DEFAULT_MCP_REGISTRY_URL


def _mcp_command_hint(server: dict) -> Optional[dict]:
    """Best-effort connection spec from a registry entry's packages/remotes,
    in the shape propose_capability(archetype='mcp_server') expects."""
    for pkg in (server.get("packages") or []):
        reg = (pkg.get("registry_name") or pkg.get("registry_type") or "").lower()
        pname = pkg.get("name") or pkg.get("identifier")
        if not pname:
            continue
        if reg in ("npm", "node"):
            return {"command": "npx", "args": ["-y", pname]}
        if reg in ("pypi", "python"):
            return {"command": "uvx", "args": [pname]}
    for rem in (server.get("remotes") or []):
        if rem.get("url"):
            return {"transport": rem.get("transport_type") or "sse", "url": rem["url"]}
    return None


def search_mcp_registry(input_: dict) -> dict:
    """Discover external MCP servers by intent (item 3). Fetches a public MCP
    registry (configurable via ABA_MCP_REGISTRY_URL) and ranks entries with
    our BM25 over name + description. A hit can be adopted as a capability via
    propose_capability(archetype='mcp_server', connection=...), then
    ensure_capability connects it live so its tools become callable."""
    q = (input_.get("query") or "").strip()
    if not q:
        return {"status": "error", "note": "search_mcp_registry needs a non-empty `query`."}
    limit = int(input_.get("limit") or 8)
    registry_url = _mcp_registry_url()
    try:
        data = _HTTP_GET_JSON(registry_url)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "note": f"Could not reach MCP registry: {e}"}
    servers = data.get("servers") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    from core.search import BM25
    by_id: dict[str, dict] = {}
    docs = []
    for i, s in enumerate(servers or []):
        name = s.get("name") or s.get("id") or f"server-{i}"
        sid = f"{i}:{name}"
        by_id[sid] = s
        docs.append((sid, f"{name} {s.get('description','')}"))
    ranked = [sid for sid, _ in BM25(docs).search(q, limit=limit)]
    out = []
    for sid in ranked:
        s = by_id[sid]
        conn = _mcp_command_hint(s)
        out.append({
            "name": s.get("name") or s.get("id"),
            "description": s.get("description"),
            "repository": (s.get("repository") or {}).get("url") if isinstance(s.get("repository"), dict) else s.get("repository"),
            "connection": conn,
            "adoptable": conn is not None,
        })
    return {"servers": out, "total_indexed": len(docs), "registry": registry_url,
            "note": "Adopt one with propose_capability(name, archetype='mcp_server', "
                    "connection={command,args} or {transport,url}); then ensure_capability "
                    "connects it and its tools become callable as 'server:tool'."}


def _detect_import_name(pip_specs: list[str]) -> str | None:
    """After a pip install, find the actual top-level IMPORT name from the
    overlay's dist-info (top_level.txt, else RECORD). Systemic: stops the agent
    guessing the import (pip 'biopython' → import 'Bio', 'GEOparse' → 'GEOparse',
    'kb-python' → 'kb_python') and thrashing on ModuleNotFoundError. Returns the
    first top-level module name, or None if undetectable."""
    import os, re, glob
    dirs = _session_site_dirs()
    if not dirs:
        return None
    _norm = lambda s: re.sub(r"[-_.]+", "-", s).lower()
    for spec in pip_specs or []:
        base = re.split(r"[<>=!~\[ ;]", (spec or "").strip())[0]
        if not base:
            continue
        target = _norm(base)
        for d in dirs:
            for di in sorted(glob.glob(os.path.join(d, "*.dist-info"))):
                stem = os.path.basename(di)[: -len(".dist-info")]   # "<name>-<version>"
                if _norm(stem.rsplit("-", 1)[0]) != target:
                    continue
                tl = os.path.join(di, "top_level.txt")
                if os.path.exists(tl):
                    for line in open(tl):
                        m = line.strip()
                        if m and not m.startswith("_"):
                            return m
                rec = os.path.join(di, "RECORD")
                if os.path.exists(rec):
                    for line in open(rec):
                        top = line.split(",", 1)[0].split("/")[0]
                        if top and "." not in top and not top.endswith(".dist-info") and not top.startswith("_"):
                            return top
    return None


def _session_site_dirs() -> list[str]:
    """The project's weft python SESSION site-packages dirs — where
    session_install lands packages (the weft replacement for the served-base pip
    overlay). Empty when no python pack is declared / the session isn't realizable."""
    try:
        from core.compute import base_env as _bev, project_env as _penv
        from core.exec.materialize import _site_paths
        from core import projects as _pj
        if not _bev.active("python"):
            return []
        pfx = _penv.prefix(str(_pj.current() or "_none"), "python")
        return [str(p) for p in _site_paths(pfx)]
    except Exception:  # noqa: BLE001
        return []


def _overlay_has_import(import_name: str) -> bool:
    """Is import_name already materialized in the project's weft session? Probes
    the session site-packages directly via PathFinder (thread-safe — never mutates
    sys.path)."""
    if not import_name:
        return False
    try:
        from importlib.machinery import PathFinder
        import importlib
        importlib.invalidate_caches()
        search = _session_site_dirs()
        return bool(search) and PathFinder.find_spec(import_name, search) is not None
    except Exception:  # noqa: BLE001
        return False


# ─── E-1 (2026-06-09): capability discovery cleanup ───────────────────────
# When `ensure_capability(name)` misses the catalog, parallel-search PyPI /
# CRAN / Bioconductor / Bioconda for an EXACT-name match (case-insensitive,
# PEP-503-normalized for PyPI) and return suggestions shaped for direct
# copy into propose_capability. Collapses the prior 4-5 round-trip
# discovery dance (ensure → not_found → list → search → propose → ensure)
# into a single round-trip on misses.
#
# Strict matching: only candidates whose canonical name matches `name`
# (case-insensitive) are returned. Fuzzy hits aren't surfaced — they'd be
# noise without a way to pick safely. The `language` param is the E-3
# plumbing already in place; today it's always None (search all sources).

def _pypi_exact(name: str) -> dict | None:
    """Strict exact-name lookup on PyPI. Returns a propose_capability-
    shaped candidate or None. Defers to search_pypi (which handles
    PEP-503 variants); we then verify the canonical name matches."""
    from .simple import search_pypi as _sp, _pep503
    try:
        res = _sp({"name": name})
    except Exception:
        return None
    if not res.get("found"):
        return None
    found = (res.get("name") or "").strip()
    # PyPI normalizes separators; accept PEP-503 equality as the strict
    # match (so "scikit-learn" vs "scikit_learn" both count).
    if _pep503(found) != _pep503(name):
        return None
    return {
        "source": "pypi",
        "archetype": "library",
        "package": found,
        "version": res.get("version"),
        "summary": res.get("summary"),
    }


def _cran_exact(name: str, timeout_s: float = 4.0) -> dict | None:
    """Strict exact-name lookup on CRAN via the crandb endpoint
    (https://crandb.r-pkg.org/<pkg> — JSON metadata, 404 if absent).
    URL is name-keyed so a 200 IS the exact-match proof."""
    import urllib.error
    import urllib.request
    from urllib.parse import quote
    try:
        req = urllib.request.Request(
            f"https://crandb.r-pkg.org/{quote(name)}",
            headers={"User-Agent": "ABA capability discovery"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    except Exception:  # noqa: BLE001
        return None
    pkg = (data.get("Package") or name).strip()
    if pkg.lower() != name.lower():
        return None
    return {
        "source": "cran",
        "archetype": "r_package",
        "package": pkg,
        "library": pkg,
        "version": data.get("Version"),
        "summary": (data.get("Title") or "").strip() or None,
    }


def _bioc_exact(name: str, timeout_s: float = 4.0) -> dict | None:
    """Strict exact-name lookup on Bioconductor (release branch). HEAD
    the package's release/bioc HTML — 200 iff the package exists with
    that exact name."""
    import urllib.error
    import urllib.request
    from urllib.parse import quote
    try:
        req = urllib.request.Request(
            f"https://bioconductor.org/packages/release/bioc/html/{quote(name)}.html",
            method="HEAD",
            headers={"User-Agent": "ABA capability discovery"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    except Exception:  # noqa: BLE001
        return None
    return {
        "source": "bioconductor",
        "archetype": "r_package",
        "package": name,
        "library": name,
        "summary": "Bioconductor release package",
    }


def _bioconda_exact(name: str) -> dict | None:
    """Strict exact-name lookup on bioconda. Defers to search_bioconda
    (already name-keyed via anaconda.org); we verify the returned name
    matches strictly before counting it as a candidate."""
    try:
        res = search_bioconda({"query": name})
    except Exception:  # noqa: BLE001
        return None
    if not res.get("found"):
        return None
    found = (res.get("name") or "").strip()
    if found.lower() != name.lower():
        return None
    return {
        "source": "bioconda",
        "archetype": "cli",
        "channel": "bioconda",
        "package": found,
        "version": res.get("latest_version"),
        "summary": res.get("summary"),
    }


# Order suggestions: prefer R sources for R-shaped names (the agent's
# common case for Bioconductor/CRAN). PyPI sits in the middle; bioconda
# (CLI) last. Within strict-name matching, this just controls UI order.
_SUGGESTION_ORDER = {"cran": 0, "bioconductor": 1, "conda": 2, "pypi": 3, "bioconda": 4}


def _conda_r_alternative(name: str) -> dict:
    """A propose_capability-shaped conda-forge candidate for an R package: the
    prebuilt 'r-<name>' binary, which bundles any system libs. The robust path
    for R packages a CRAN/Bioc source compile can't build because they need a
    system library (hdf5r→HDF5, sf→GDAL). conda R is global-only, so this
    installs into the SHARED R base, not a per-project library."""
    return {"source": "conda", "archetype": "r_package",
            "package": f"r-{name.lower()}", "library": name, "version": "latest",
            "summary": (f"{name} as a prebuilt conda-forge R binary (r-{name.lower()}) — "
                        f"bundles system libs; use when a source compile fails on a "
                        f"missing system library. Installs into the shared R base.")}


def _search_external_for_name(name: str,
                              language: str | None = None,
                              total_timeout_s: float = 4.0) -> list[dict]:
    """Parallel strict-exact search of external registries. Returns a
    list of zero or more propose_capability-shaped candidates.

    `language` filters which sources to query (None = all). The arg is
    kept for E-3 wiring; today `ensure_capability` doesn't pass it."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeoutError
    sources: dict[str, callable] = {}
    if language in (None, "python"):
        sources["pypi"] = lambda: _pypi_exact(name)
    if language in (None, "r"):
        sources["cran"] = lambda: _cran_exact(name)
        sources["bioconductor"] = lambda: _bioc_exact(name)
    if language in (None, "cli"):
        sources["bioconda"] = lambda: _bioconda_exact(name)
    if not sources:
        return []

    candidates: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = {ex.submit(fn): key for key, fn in sources.items()}
        deadline = total_timeout_s
        for fut in list(futs):
            try:
                c = fut.result(timeout=deadline)
            except (FTimeoutError, TimeoutError):
                continue
            except Exception:  # noqa: BLE001
                continue
            if c is not None:
                candidates.append(c)
    # If it resolved as an R package, also offer the conda-forge binary as an
    # alternative — the robust path when a CRAN/Bioc source compile fails on a
    # missing system library (hdf5r→HDF5). (conda R is global/shared-base.)
    if any(c.get("source") in ("cran", "bioconductor") for c in candidates):
        candidates.append(_conda_r_alternative(name))
    candidates.sort(key=lambda c: _SUGGESTION_ORDER.get(c.get("source", ""), 9))
    return candidates


def _r_module_block() -> dict | None:
    """R is the r-bio MODULE (misc/modules.md). If it's turned OFF (and not already
    present), refuse to auto-install and tell the agent to ASK the user to enable it —
    the whole point of an off toggle. Returns a blocked result, or None to proceed
    (On / First use → auto-install is expected; already-ready → nothing to gate)."""
    try:
        from core.modules import registry, manager
        spec = registry.get("r-bio")
        if spec and manager.mode(spec) == "off" and manager.actual_state(spec) != "ready":
            return {
                "status": "blocked", "name": "R toolchain", "module": "r-bio",
                "note": ("The R toolchain is turned OFF, so I did NOT install it. Ask the user "
                         "to enable it by calling `ask_clarification(question=\"…\", "
                         "enable_module=\"r-bio\")` — that shows one-click Enable buttons "
                         "(On / First use). Do NOT paste Settings instructions or work around "
                         "the off setting; once they enable it, re-run this."),
            }
    except Exception:  # noqa: BLE001 — the gate must never itself break provisioning
        pass
    return None


def _default_probe_python() -> str | None:
    """The project's weft SESSION python that import-probes + installs run
    against, or None when NO python base pack is declared (a python-less
    deployment — the caller degrades). A weft error when a pack IS declared but
    the session won't realize PROPAGATES (it is NOT swallowed into None): the
    old swallow silently diverted installs onto the served-base/micromamba path
    on a transient weft hiccup — the exact hybrid-revival bug W3.5 removes."""
    from core import projects
    from core.compute import base_env, project_env
    if not base_env.active("python"):
        return None
    return str(project_env.interpreter(str(projects.current() or "_none"), "python"))



def _r_version_in_session(pid: str, libname: str) -> str | None:
    """packageVersion() against the PROJECT SESSION's R (pack mode)."""
    import subprocess
    from core.compute import project_env
    try:
        rs = project_env.interpreter(str(pid), "r")
        r = subprocess.run([str(rs), "-e",
                            f'cat(as.character(packageVersion("{libname}")))'],
                           capture_output=True, text=True, timeout=120)
        v = (r.stdout or "").strip()
        return v if r.returncode == 0 and v else None
    except Exception:  # noqa: BLE001
        return None


def _ensure_r_via_session(cap: dict, input_: dict, ctx: dict | None,
                          name: str) -> dict:
    """W3.4 pack mode: R capability into the PROJECT's session over the R base
    pack. conda-first (binary r-*/bioconductor-* into the session — live, no
    compile); github/source via the CAPTURED session installer (rides
    snapshots as a portable post_install step). The shared pack is never
    mutated — additions live in the project session."""
    from core import projects
    from core.compute import project_env
    from core.exec import r as rexec
    pid = str(projects.current() or "default")
    rp = dict((cap.get("provisioning") or {}).get("r") or {})
    for _k in ("ref", "source", "package"):
        if input_.get(_k):
            rp[_k] = input_[_k]
    _src = rp.get("source", "cran")
    _pkg = rp.get("package") or cap.get("name")
    libname = rp.get("library") or (
        _pkg.split("/")[-1] if _src == "github"
        else (_pkg[2:] if _src == "conda" and _pkg.startswith("r-") else _pkg))
    min_version = (str(input_.get("min_version") or rp.get("min_version") or "").strip() or None)
    force = bool(input_.get("force")) or any(input_.get(_k) for _k in ("ref", "source", "package"))
    installed = _r_version_in_session(pid, libname)
    if installed and not force and (not min_version or rexec.version_ge(installed, min_version)):
        return {"status": "ready", "name": cap.get("name"), "archetype": "r_package",
                "library": libname, "version": installed,
                "note": f"Already available — library({libname}) {installed} works in run_r."}
    try:
        if _src in ("cran", "bioconductor", "conda"):
            conda_name = _pkg if _pkg.startswith(("r-", "bioconductor-")) else (
                f"bioconductor-{_pkg.lower()}" if _src == "bioconductor" else f"r-{_pkg.lower()}")
            try:
                project_env.install(pid, "r", [conda_name], eco="conda")
            except Exception:  # noqa: BLE001 — no conda build → captured source install
                _cmd = (f"Rscript -e 'if (!requireNamespace(\"BiocManager\", quietly=TRUE)) "
                        f"install.packages(\"BiocManager\"); BiocManager::install(\"{_pkg}\", "
                        f"update=FALSE, ask=FALSE)'" if _src == "bioconductor" else
                        f"Rscript -e 'install.packages(\"{_pkg}\")'")
                project_env.run_installer(pid, "r", _cmd,
                                          note=f"{_src} install of {_pkg} (no conda binary)")
        elif _src == "github":
            _ref = f', ref="{rp.get("ref")}"' if rp.get("ref") else ""
            project_env.run_installer(
                pid, "r",
                f"Rscript -e 'if (!requireNamespace(\"remotes\", quietly=TRUE)) "
                f"install.packages(\"remotes\"); remotes::install_github(\"{_pkg}\"{_ref}, "
                f"upgrade=\"never\", force={str(force).upper()})'",
                note=f"github install of {_pkg}")
        else:
            return {"status": "error", "name": name,
                    "note": f"unknown R source {_src!r} (cran|bioconductor|conda|github)"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "name": name, "archetype": "r_package",
                "note": f"R install into the project env failed: {e}"}
    new_ver = _r_version_in_session(pid, libname)
    if not new_ver:
        return {"status": "error", "name": name, "archetype": "r_package",
                "library": libname,
                "note": f"Installed, but library({libname}) is not loadable in the "
                        f"project R env — NOT marking ready."}
    # a stale loaded namespace in the running R kernel can pin the old build
    rexec.r_unload_namespace(libname, (ctx or {}).get("thread_id"))
    return {"status": "ready", "name": cap.get("name"), "archetype": "r_package",
            "library": libname, "version": new_ver,
            "note": f"Installed into the project R env; library({libname}) {new_ver} "
                    f"is usable in run_r now."}


def _extend_into_named_env(env_name: str, packages: list[str], cap: dict) -> dict:
    """Layer `packages` into a named isolated env via extends_env (frozen
    identities: a new EnvID is minted, the old id kept in history). The env's
    language picks the ecosystem (pypi | cran)."""
    from core.compute import named_envs
    from core.compute.errors import ComputeError
    from core import projects
    pid = str(projects.current() or "default")
    try:
        res = named_envs.extend(pid, env_name, list(packages))
    except ComputeError as e:
        return {"status": "error", "name": cap.get("name"), "env": env_name,
                "error": e.to_payload(),
                "note": f"could not extend env '{env_name}': {e.detail or e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "name": cap.get("name"), "env": env_name,
                "note": f"could not extend env '{env_name}': {e}"}
    # Identity changed → a kernel still running on the OLD realization would
    # never see the new packages (found live: post-extend in-session imports
    # failed while the registry was correct). Restart the env's live sessions.
    restarted = _evict_env_kernels(env_name)
    note = (f"Installed {', '.join(packages)} into isolated env '{env_name}' "
            f"(new env_id {res['env_id']}). Frozen identities: extending mints a "
            f"new id; history kept. Run in it with run_python(env='{env_name}', …).")
    if restarted:
        note += (" NOTE: the env's running session was restarted to pick up the "
                 "new packages — in-memory objects from earlier steps in this "
                 "env are gone; reload what you need.")
    return {"status": "ready", "name": cap.get("name"), "env": env_name,
            "env_id": res["env_id"], "installed": list(packages), "note": note}


def ensure_capability(input_: dict, ctx: dict | None = None) -> dict:
    """Materialize a catalogued capability on demand (P1). Python libraries go
    into the wipeable overlay so the next run_python can import them; non-pip
    CLI tools (conda) are reported as deferred. A long install is cancellable
    (Stop) via the turn's cancel_token, and streams phase progress."""
    name = (input_.get("name") or input_.get("capability") or "").strip()
    if not name:
        return {"error": "name is required"}
    # env= targets a named ISOLATED env instead of the default session; a package
    # install extends it (new EnvID, history kept). Validate up front — an unknown
    # name never silently falls back to the default env.
    env = (input_.get("env") or "").strip() or None
    if env is not None:
        from core.compute import named_envs as _ne
        from core import projects as _proj
        if _ne.resolve(str(_proj.current() or "default"), env) is None:
            return {"status": "error", "name": name, "env": env,
                    "note": f"No named env '{env}' in this project. Call inspect_env() to "
                            f"see the named-env catalog, or make_isolated_env to create it."}
    _ct = (ctx or {}).get("cancel_token")
    from core.catalog import resolve_capability
    cap = resolve_capability(name)
    # env= is an UNAMBIGUOUS "install this package INTO env X" — handle it EARLY,
    # before the default-env machinery (already-importable probe, pack-provider
    # recognition, cluster modules). Those all answer "is it available in the
    # DEFAULT stack / how do I get it there", which is the wrong question for a
    # named target: a package present in the default env would short-circuit and
    # env X would never gain it (found live: ensure_capability(env='nptools',
    # 'pandas') returned ready while nptools stayed numpy-only). For a package
    # capability we extend with its declared specs; for an UNCATALOGUED name we
    # extend with the raw name (the env's language picks pypi vs cran) — weft's
    # solver resolves it. A NON-package capability keeps env='s scope note.
    if env is not None:
        _prov = (cap or {}).get("provisioning") or {}
        _pkgs = None
        if _prov.get("pip"):
            _pkgs = list(_prov["pip"])
        elif _prov.get("conda"):
            _c = _prov["conda"]
            _spec = _c["spec"] if isinstance(_c, dict) else _c
            _pkgs = [_spec] if isinstance(_spec, str) else list(_spec)
        elif (cap or {}).get("archetype") == "r_package":
            _pkgs = [(cap or {}).get("package") or (cap or {}).get("library") or name]
        elif not cap or (cap or {}).get("archetype") in (None, "library"):
            _pkgs = [name]   # uncatalogued / plain library → install by name
        if _pkgs is not None:
            return _extend_into_named_env(env, _pkgs, cap or {"name": name})
        # a non-package capability (MCP server, reference): fall through, note it
        _env_scope_note = (f" (env={env!r} applies to PACKAGE installs only; this "
                           f"capability is not a package — provisioned normally.)")
    else:
        _env_scope_note = ""
    # Cluster module provider (prefer:first, job-path scope): does a cluster module
    # satisfy this tool by exact name? ONLY for CLI/binary tools — NEVER for a pip
    # library. `resolve()` matches any exact-name Lmod module, and on some clusters a
    # python PACKAGE is exposed as a module (e.g. `scanpy/1.4.4-...-python-3.6.6`)
    # that drags in its OWN ancient Python; recording it makes every background job
    # `module load` it and shadow the conda env's numpy (the prj_6d986f40 incident).
    # Pip libraries are satisfied in the conda env, so skip the module for them and
    # keep it only for catalogued CLI tools + uncatalogued binaries (cellranger, …).
    _mod = None
    _is_pip_lib = bool((cap or {}).get("provisioning", {}).get("pip")) or \
        (cap or {}).get("archetype") == "library"
    try:
        from core.exec import modules as _modprov
        if _modprov.modules_active() and not _is_pip_lib:
            _mod = _modprov.resolve(name)
            if _mod:
                from core import projects as _projects
                _modprov.record_project_module(_projects.current(), _mod)
                # make the tool usable IN-PROCESS now — prepend the module's binary
                # env-delta to the live kernel so run_python subprocesses find its
                # binary (no background job / restart needed just to load it).
                _snip = _modprov.kernel_env_snippet(_mod)
                _tid = (ctx or {}).get("thread_id")
                if _snip and _tid:
                    from core.exec.kernels import get_pool
                    _s = get_pool().peek(str(_tid), "python")
                    if _s is not None:
                        _s.execute(_snip, timeout_s=20)
    except Exception:  # noqa: BLE001
        _mod = None
    if not cap:
        if _mod:
            # Uncatalogued but a cluster module provides it (e.g. cellranger):
            # prefer:first → satisfy via the module (recorded above for the
            # project's background jobs) rather than suggest a slower pip/conda
            # install. Run it from a backgrounded run (Slurm step).
            return {"status": "ready", "name": name, "archetype": "cli", "module": _mod,
                    "note": f"Provided by cluster module '{_mod}', loaded in background "
                            f"Slurm jobs; not installed in-process. Invoke it from "
                            f"run_python(background=True) / a Slurm step."}
        # (A) Already importable? An uncatalogued name can still be satisfied — a
        # core/base package or one a prior session materialized into the overlay.
        # Verify a REAL import on the runtime path BEFORE routing to external
        # registries; if it loads, the capability the agent needs (to `import` it)
        # is already there — returning "candidates" here was the bug that made the
        # agent try to re-install (or bail on) a package it already had.
        # verify_python_imports (not find_spec): a present-but-unloadable package
        # has a spec but explodes on import. Probe names: the name itself (if a
        # plausible identifier — a pip name like `scikit-learn` isn't one) PLUS
        # any import aliases the env packs declare for it (#11: asked by package
        # name, probed by real import name).
        _probes = [name] if name.isidentifier() else []
        try:
            from core.compute import env_packs as _ep
            _probes += [a for a in _ep.import_names_for_package(name)
                        if a not in _probes]
        except Exception:  # noqa: BLE001
            pass
        if _probes:
            from core.exec.verify import verify_python_imports
            try:
                _probe_py = _default_probe_python()
            except Exception:  # noqa: BLE001 — no realizable session → skip the shortcut
                _probe_py = None
            if _probe_py is not None:
                for _p in _probes:
                    _ok, _ = verify_python_imports([_p], python_exe=_probe_py)
                    if _ok:
                        return {"status": "ready", "name": name, "import_name": _p,
                                "note": f"Already available — `import {_p}` works in run_python "
                                        f"(provided by the base env or a prior install); no install needed."}
        # (B) Declared by an env pack? (#11 already-provided recognition.) The
        # bundle's env packs declare base contents + import aliases; if a pack
        # provides this name, the answer is that pack — NEVER an external
        # registry, where a same-name hit is often an unrelated package.
        try:
            from core.compute import env_packs as _ep
            _provider_packs = _ep.packs_providing(name)
            for _p in _probes:
                _provider_packs += [x for x in _ep.packs_providing(_p)
                                    if x not in _provider_packs]
        except Exception:  # noqa: BLE001
            _provider_packs = []
        if _provider_packs:
            return {"status": "provided_by_pack", "name": name,
                    "packs": _provider_packs,
                    "import_name": _probes[0] if _probes else None,
                    "note": (f"'{name}' is declared by the environment pack(s) "
                             f"{', '.join(repr(p) for p in _provider_packs)} — it is part of a "
                             f"curated base, not something to install from an external "
                             f"registry. If the import failed just now, the pack isn't "
                             f"materialized yet: enable/materialize it (Settings → Modules, "
                             f"or ask the user), then retry.")}
        # E-1: parallel-search external registries for an exact-name match
        # instead of pointing at list_capabilities (which would also be
        # empty for an uncatalogued name). Returns suggestions shaped for
        # direct copy into propose_capability.
        suggestions = _search_external_for_name(name)
        if suggestions:
            return {
                "status": "candidates",
                "name": name,
                "installable": True,
                "suggestions": suggestions,
                "note": (
                    f"'{name}' isn't pre-catalogued, but it IS installable — this is the "
                    "normal two-step path, not a dead end: pick the suggestion whose "
                    "`summary` matches what you actually need, call propose_capability with "
                    "its fields (already shaped for it), then re-call ensure_capability. "
                    "CAUTION — these are NAME matches across ecosystems, and a shared name "
                    "can be an UNRELATED package: verify each candidate's `summary` fits "
                    "your use before proposing (a PyPI hit is often a namesake), and mind "
                    "the language — a CRAN/Bioconductor match runs via the R kernel, a PyPI "
                    "match via Python."),
            }
        return {
            "status": "not_found", "name": name,
            "note": (
                f"No capability '{name}' in the catalog, and no exact "
                f"match found on PyPI / CRAN / Bioconductor / bioconda. "
                f"If you know the install source, call propose_capability "
                f"directly (e.g. source='github', package='owner/repo')."),
        }
    # Honor the lifecycle: an unapproved (proposed) capability isn't runnable
    # until approved (the 'ask' multi-user gate).
    if cap.get("status") not in (None, "published"):
        return {"status": "awaiting_approval", "name": cap.get("name"),
                "note": f"'{name}' is proposed but not yet approved; it can't be "
                        f"materialized until approval."}
    # Reference catalogue entry: know-how mined offline, not a runnable
    # artifact in ABA. Don't pretend to install it.
    if cap.get("reference"):
        return {"status": "reference", "name": cap.get("name"),
                "origin": cap.get("origin"), "source_ref": cap.get("source_ref"),
                "note": f"'{cap.get('name')}' is a reference entry extracted from "
                        f"{cap.get('origin')} — it describes an approach, it isn't "
                        f"runnable here. Implement it with ABA capabilities (search the "
                        f"catalogue / propose_capability for the real libraries), using "
                        f"read_capability for its inputs."}
    # Role-aware framing (#11): a viewer/converter is used differently from a
    # library — say so on every ready response, so the agent doesn't try to
    # `import` a viewer or hand a converter to the user as an app. Provisioning
    # below is role-agnostic (a converter is often just a library to install).
    from core.catalog import capability_role
    _role = capability_role(cap)
    _role_note = ""
    # Set when env= was passed for a NON-package capability (below): env applies
    # to package installs only, so the capability is provisioned normally and this
    # note tells the agent why the env target was ignored.
    _env_scope_note = ""
    if _role == "viewer":
        _vb = cap.get("viewer") or {}
        _opens = ", ".join(list(_vb.get("extensions") or []) +
                           list(_vb.get("entity_types") or [])) or "its declared formats"
        _role_note = (f" ROLE: viewer — it opens {_opens} visually for the USER "
                      f"(offered on matching entities' Open-with); it is not an "
                      f"importable analysis library.")
    elif _role == "converter":
        _cb = cap.get("converter") or {}
        _role_note = (f" ROLE: converter — transforms "
                      f"{', '.join(_cb.get('from') or ['?'])} → "
                      f"{', '.join(_cb.get('to') or ['?'])}; use it to change formats, "
                      f"typically feeding a viewer or another tool.")

    def _ready(payload: dict) -> dict:
        payload.setdefault("role", _role)
        if _role_note and payload.get("status") == "ready":
            payload["note"] = (payload.get("note") or "") + _role_note
        if _env_scope_note:
            payload["note"] = (payload.get("note") or "") + _env_scope_note
        return payload

    from core.runtime import progress
    progress.emit(f"Materializing '{cap.get('name')}'…", phase="ensure")
    prov = cap.get("provisioning") or {}
    # env= for a PACKAGE was already handled early (before the default-env
    # machinery). Only a NON-package env= capability reaches here; `_env_scope_note`
    # (set above) is appended to its result so the agent learns env= was ignored.
    if prov.get("pip"):
        # Already importable? Then ensuring is a no-op. Two cases, both keyed on
        # the seed's explicit import_name (so we never short-circuit a package
        # that merely shares a base dep):
        #   • base env — scanpy/anndata/… ship in the .venv scientific stack;
        #   • overlay  — a prior session already materialized it (e.g. scvi-tools).
        # Skipping avoids a `pip --target` that re-resolves + re-fetches the whole
        # dependency tree of a heavy package every fresh session.
        # Already importable? Decide by a REAL import on the runtime path (base +
        # overlay), not PathFinder.find_spec — a present-but-unloadable package
        # (wrong-numpy ABI, partial install, missing system lib) HAS a spec but
        # explodes on import (the tensorflow incident). verify, don't presume.
        from core.exec.verify import verify_python_imports
        from core.compute.errors import ComputeError
        from core import projects as _projects
        from core.compute import project_env as _penv
        try:
            _probe_py = _default_probe_python()
        except (ComputeError, RuntimeError) as ce:
            return {"status": "error", "name": name,
                    "note": f"the python environment pack is not available: {ce}"}
        if _probe_py is None:
            return {"status": "error", "name": name,
                    "note": "no python environment pack is declared for this deployment"}
        _imp0 = cap.get("import_name")
        if _imp0:
            _ok, _ = verify_python_imports([_imp0], python_exe=_probe_py)
            if _ok:
                return _ready({"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                        "archetype": cap.get("archetype"), "import_name": _imp0,
                        "note": f"Already available; `import {_imp0}` works in run_python."})
        try:
            # Install LIVE into the project's default weft session
            # (session_install) — the running kernel imports it after the cache
            # invalidation below (no restart); the next background job's snapshot
            # picks it up as a frozen EnvID.
            _penv.install(str(_projects.current() or "_none"), "python",
                          list(prov["pip"]), eco="pypi")
        except Exception as e:  # noqa: BLE001
            # Solve-driven placement (env_refactor.md): if the constrained install
            # is UNSAT against the pinned base (the package needs versions the
            # base forbids — the tensorflow/numpy-1.x case), DON'T fail or corrupt
            # the base — auto-route to an ISOLATED env the agent can use.
            if _is_constraint_conflict(str(e)):
                return _auto_isolate(name, list(prov["pip"]), cap)
            # Capture the full stack — unlike the job worker, this path only
            # stored str(e), so the `Permission denied: ''` class was undiagnosable
            # from the agent-facing note. Log it for the server console.
            import traceback as _tb
            print(f"[ensure_capability] materialize failed for {name!r}:\n{_tb.format_exc()}", flush=True)
            return {"status": "error", "name": name, "note": f"materialization failed: {e}"}
        # Authoritatively resolve the import name (seed override → auto-detect),
        # so the agent never guesses `import <pipname>` and thrashes.
        imp = cap.get("import_name") or _detect_import_name(list(prov["pip"]))
        # Verify the install actually LOADS before claiming ready — no more
        # "ready"-lies for ABI-broken / partial installs.
        if imp:
            _ok, _detail = verify_python_imports([imp], python_exe=_probe_py)
            if not _ok:
                return {"status": "error", "name": name, "import_name": imp,
                        "note": (f"Installed, but `import {imp}` fails to load — likely an ABI "
                                 f"mismatch (built against a different numpy), a partial install, "
                                 f"or a missing system library. NOT marking ready."),
                        "detail": _detail}
        note = "Installed into the project's weft session; importable from run_python now."
        if imp:
            note += f" Import it with `import {imp}`."
        else:
            note += (" If `import " + str(cap.get("name")) + "` fails, the import name "
                     "differs from the package name — confirm it with inspect_package "
                     "rather than guessing/retrying.")
        # If a Python kernel is already running for this thread, it scanned the
        # overlay at startup (before this install) and importlib cached the dir
        # listing — so `import <new pkg>` would fail until a restart. Invalidate
        # its caches now so the very next run_python imports it WITHOUT a restart
        # (the harmonypy-needed-restart friction).
        try:
            _tid = (ctx or {}).get("thread_id")
            if _tid:
                from core.exec.kernels import get_pool
                _sess = get_pool().peek(str(_tid), "python")
                if _sess is not None:
                    _sess.execute("import importlib as _il; _il.invalidate_caches()", timeout_s=15)
        except Exception:  # noqa: BLE001
            pass
        return _ready({"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                "archetype": cap.get("archetype"), "import_name": imp, "note": note})
    if prov.get("conda"):
        if (cap.get("archetype") == "r_package"):   # R via conda is still the r-bio module
            _blk = _r_module_block()
            if _blk:
                return _blk
        # `_mod` (resolved + recorded up top) means a cluster module also covers this
        # CLI tool — it's loaded in the project's background jobs. The conda spec
        # lands LIVE in the project's weft session (its bin/ is on the kernel PATH
        # via the session prefix); a failure is non-fatal when `_mod` covers it.
        def _mod_covered(reason: str):
            return _ready({"status": "ready", "name": cap.get("name"),
                    "version": cap.get("version"), "archetype": cap.get("archetype"),
                    "module": _mod, "note": f"Provided by cluster module '{_mod}' "
                    f"(loaded in background Slurm jobs); the local conda install {reason}."})
        try:
            _probe_py = _default_probe_python()
        except Exception:  # noqa: BLE001 — no realizable session; handled below
            _probe_py = None
        if _probe_py is None:
            if _mod:
                return _mod_covered("isn't needed there (no local python pack)")
            return {"status": "error", "name": name,
                    "note": "no realizable python environment pack for a conda install"}
        try:
            from core import projects as _projects
            from core.compute import project_env as _penv
            _c = prov["conda"]
            _spec = _c["spec"] if isinstance(_c, dict) else _c
            _penv.install(str(_projects.current() or "_none"), "python",
                          [_spec] if isinstance(_spec, str) else list(_spec),
                          eco="conda")
        except Exception as e:  # noqa: BLE001
            if _mod:
                return _mod_covered(f"isn't needed there and failed: {e}")
            return {"status": "error", "name": name, "note": f"conda install failed: {e}"}
        _note = ("Installed into the project's weft session; the binary is on PATH — "
                 "invoke it from run_python via subprocess.")
        if _mod:
            _note += f" Background Slurm jobs also load cluster module '{_mod}'."
        return _ready({"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                "archetype": cap.get("archetype"), "note": _note, "module": _mod})
    if prov.get("mcp_server"):
        # Live adoption: connect the external server now so its tools become
        # callable as 'server:tool' for the rest of this session.
        conn = prov["mcp_server"]
        if conn.get("url"):
            return {"status": "deferred", "name": cap.get("name"), "archetype": "mcp_server",
                    "note": "Remote (HTTP/SSE) MCP transport isn't wired yet; only stdio "
                            "(command/args) servers can be connected on demand."}
        from core.runtime.mcp import add_server, ServerConfig
        progress.emit(f"Connecting MCP server '{cap.get('name')}'…", phase="mcp")
        cfg = ServerConfig(
            name=cap.get("name"),
            command=conn.get("command"),
            args=tuple(conn.get("args") or ()),
            env={str(k): str(v) for k, v in (conn.get("env") or {}).items()},
            cwd=conn.get("cwd"),
        )
        res = add_server(cfg)
        if res.get("status") in ("connected", "already_connected"):
            tools = res.get("tools") or []
            return _ready({"status": "ready", "name": cap.get("name"), "archetype": "mcp_server",
                    "tools": tools,
                    "note": f"Connected; {len(tools)} tool(s) now callable: "
                            f"{', '.join(tools[:8])}{'…' if len(tools) > 8 else ''}."})
        return {"status": "error", "name": cap.get("name"), "archetype": "mcp_server",
                "note": f"Could not connect MCP server: {res.get('note')}"}
    if prov.get("pipeline"):
        pl = prov["pipeline"]
        engine = (pl.get("engine") or "nextflow").lower()
        if engine != "nextflow":
            return {"status": "deferred", "name": cap.get("name"), "archetype": "pipeline",
                    "note": f"Pipeline engine '{engine}' isn't wired yet (only nextflow)."}
        from core.compute import named_envs
        try:
            named_envs.ensure_tool_env(["nextflow"], name="aba-tool-nextflow",
                                       probe="nextflow -version",
                                       channels=["bioconda", "conda-forge"])
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": cap.get("name"), "archetype": "pipeline",
                    "note": f"Could not provision nextflow: {e}"}
        ref = pl.get("nf_core") or cap.get("name")
        return _ready({"status": "ready", "name": cap.get("name"), "archetype": "pipeline",
                "note": f"nextflow provisioned (weft tool env). Run this pipeline with "
                        f"run_nextflow(pipeline='{ref}', profile='test', ...) — it puts "
                        f"nextflow on PATH from the cached env. "
                        f"(Large runs will route to HPC/remote later — local only for now.)"})
    if prov.get("r"):
        # Module gate (misc/modules.md): R is the r-bio module — honor an OFF toggle
        # by asking the user instead of silently installing the toolchain.
        _blk = _r_module_block()
        if _blk:
            return _blk
        # W3.4 pack mode: the R pack + project session replace the r-bio shell
        # toolchain — install into the session (conda-first, captured escape
        # hatch), never the shared base.
        # weft-only: the R pack + project session ARE the R toolchain — install
        # into the session (conda-first, captured installer escape hatch). REQUIRED:
        # there is no tools-env/micromamba R fallback anymore.
        from core.compute import base_env as _bev
        from core.compute.errors import ComputeError
        try:
            _bev.require("r")
        except (ComputeError, RuntimeError) as ce:
            return {"status": "error", "name": name,
                    "note": f"the R environment pack is not available: {ce}"}
        return _ready(_ensure_r_via_session(cap, input_, ctx, name))
    return {"status": "error", "name": name, "note": "capability has no recognized provisioning."}


def propose_capability_tool(input_: dict) -> dict:
    """Add a new Python library to the catalog on demand (P2′ demand loop).
    De-dupes against the existing catalog, then proposes it; in auto-approval
    mode it's published immediately (and audited). Follow with ensure_capability
    to install it. For libraries whose import name differs from the pip name
    (e.g. scikit-image → skimage), pass import_name so the ready note is correct."""
    name = (input_.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    from core.catalog import (resolve_capability, propose_capability as _propose,
                              update_capability, capability_status)

    archetype = (input_.get("archetype") or "library").strip()
    version = str(input_.get("version") or "latest")
    if archetype == "mcp_server":
        # An external MCP server discovered via search_mcp_registry. Provisioning
        # carries the connection spec; ensure_capability connects it live.
        conn = input_.get("connection") or {}
        if not isinstance(conn, dict) or not (conn.get("command") or conn.get("url")):
            return {"status": "error", "name": name,
                    "note": "mcp_server needs connection={command,args[,env]} (stdio) "
                            "or {transport,url} (remote)."}
        spec = {
            "name": name, "version": version, "archetype": "mcp_server",
            "summary": input_.get("summary") or f"{name} (MCP server, adopted on demand)",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"mcp_server": conn},
            "source": input_.get("source") or "mcp_registry",
        }
    elif archetype == "pipeline":
        # An nf-core (or similar) pipeline discovered via search_nf_core. Record
        # only for now — running needs a Nextflow runtime (deferred).
        spec = {
            "name": name, "version": version, "archetype": "pipeline",
            "summary": input_.get("summary") or f"{name} (nf-core pipeline, catalogued)",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"pipeline": {
                "engine": "nextflow",
                "nf_core": name,
                "url": input_.get("url") or f"https://nf-co.re/{name}",
                "revision": input_.get("revision") or version,
            }},
            "source": input_.get("source") or "nf-core",
        }
    elif archetype == "r_package":
        # An R package (r_provisioning.md). Native install into the project R
        # library from CRAN / Bioconductor / GitHub. `name` is the capability
        # name; `package` the install target (owner/repo for github); `library`
        # the R library() name used for the present-check (defaults sensibly).
        r_source = (input_.get("source") or "cran").strip()
        pkg = (input_.get("package") or name).strip()
        if r_source == "github":
            lib = input_.get("library") or pkg.split("/")[-1]
        elif r_source == "conda":           # conda 'r-foo' → R library 'foo'
            lib = input_.get("library") or (pkg[2:] if pkg.startswith("r-") else pkg)
        else:
            lib = input_.get("library") or pkg
        from core.exec.r import validate_install
        verr = validate_install(r_source, pkg, input_.get("ref"))
        if verr:
            return {"status": "error", "name": name, "note": verr}
        spec = {
            "name": name, "version": version, "archetype": "r_package",
            "summary": input_.get("summary") or f"{name} (R package from {r_source})",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"r": {"source": r_source, "package": pkg,
                                   "library": lib, "ref": input_.get("ref")}},
            "source": r_source,
        }
    elif archetype == "cli":
        # A command-line tool from a conda channel (e.g. bowtie2, bedtools).
        channel = input_.get("channel") or "bioconda"
        conda_spec = f"{name}={version}" if version and version != "latest" else name
        spec = {
            "name": name, "version": version, "archetype": "cli",
            "summary": input_.get("summary") or f"{name} (added on demand from {channel})",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"conda": {"channel": channel, "spec": conda_spec}},
            "source": channel,
        }
    else:
        spec = {
            "name": name, "version": version, "archetype": "library",
            "summary": input_.get("summary") or f"{name} (added on demand from PyPI)",
            "domain_tags": input_.get("tags") or [],
            "provisioning": {"pip": [name]},
            "source": "pypi",
        }
        if input_.get("import_name"):
            spec["import_name"] = input_["import_name"]

    # De-dupe: if it already exists, UPDATE a project/user-scoped entry in place
    # (so the agent can correct a wrong git ref / source it just proposed)
    # instead of silently keeping the stale one. Curated system/installation
    # catalog entries are left untouched.
    existing = resolve_capability(name)
    if existing:
        scope = str(existing.get("scope", "system"))
        if (scope.startswith("project") or scope.startswith("user")) and update_capability(name, spec):
            return {"status": "updated", "name": name, "archetype": archetype,
                    "note": "Updated the existing catalog entry (e.g. corrected the "
                            "source / git ref / provisioning). Call ensure_capability to (re)install."}
        return {"status": "already_available", "name": existing.get("name"),
                "version": existing.get("version"),
                "note": "Already in the catalog as a curated entry — re-proposing can't "
                        "modify it. Install it with ensure_capability (pass ref= / source= / "
                        "package= to override the branch/source for THIS install), or propose "
                        "a differently-named project variant."}

    cap_id = _propose(spec)
    if capability_status(cap_id) != "published":
        return {"status": "pending_approval", "name": name,
                "note": "Proposed; awaiting approval before it can be installed."}
    if archetype == "cli":
        note = ("Added to the catalog (auto-approved). Call ensure_capability to "
                "install it; the binary will be on PATH — invoke it from run_python via subprocess.")
    elif archetype == "mcp_server":
        note = ("Added to the catalog (auto-approved). Call ensure_capability to "
                "connect it live; its tools then appear as 'server:tool' and are callable this session.")
    elif archetype == "pipeline":
        note = ("Catalogued (auto-approved). Running it needs a Nextflow runtime "
                "(not yet wired) — ensure_capability will report it as deferred.")
    elif archetype == "r_package":
        note = ("Added to the catalog (auto-approved). Call ensure_capability to "
                "install it into the project R library, then use library(...) in run_r.")
    else:
        note = ("Added to the catalog (auto-approved). Call ensure_capability to "
                "install it, then import it in run_python.")
    return {"status": "approved", "name": name, "archetype": archetype, "note": note}


def fetch_url(input_: dict, ctx: dict | None = None) -> dict:
    """Download a URL into the project's fetch scratch (P4). Size-gated + audited."""
    import urllib.request
    from core.data.workspace import scratch_dir
    from core.graph.audit import log_event
    from core import projects

    url = (input_.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    filename = input_.get("filename") or url.split("?")[0].rstrip("/").split("/")[-1] or "download"
    project_id = projects.current() or "default"
    dest = scratch_dir(str(project_id), "fetch") / filename
    threshold = 5 * 1024 ** 3
    mode = config.settings.capability_approval.get()
    # Some hosts (e.g. Bioconductor) 403 the default urllib user-agent.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ABA)"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            clen = int(resp.headers.get("Content-Length") or 0)
            if clen > threshold and mode == "ask":
                return {"status": "needs_approval", "url": url, "bytes": clen,
                        "note": f"Download is ~{clen} bytes (over threshold); approval required in ask mode."}
            total = 0
            with open(dest, "wb") as f:
                for chunk in iter(lambda: resp.read(1 << 20), b""):
                    f.write(chunk)
                    total += len(chunk)
    except Exception as e:  # noqa: BLE001
        return {"error": f"fetch failed: {e}"}
    log_event("data_fetched", title=filename, detail={"url": url, "bytes": total, "path": str(dest)})
    return {"status": "ok", "path": str(dest), "filename": filename, "bytes": total}


def lookup_sra_runinfo(input_: dict, ctx: dict | None = None) -> dict:
    """Run table for a sequencing-run/study accession via the ENA filereport API
    (P4). GEO accessions are redirected to the GEO recipe, not dead-ended."""
    import json as _json
    import urllib.request
    acc = (input_.get("accession") or input_.get("query") or "").strip()
    if not acc:
        return {"error": "accession is required"}
    # GEO series/sample accessions are not in ENA's read_run index — this tool
    # would 400/return empty. Redirect to discovery instead of dead-ending (the
    # wrong-tool reach that made the agent scrape GEO by hand).
    if acc.upper().startswith(("GSE", "GSM", "GDS", "GPL")):
        return {"status": "wrong_tool", "accession": acc,
                "note": (f"{acc} is a GEO accession; this tool only handles SRA/ENA "
                         "run/study accessions (SRR/SRP/ERR/PRJNA…) and can't list a "
                         "GEO study's samples or metadata. To list samples / fetch "
                         "processed matrices, call search_skills('fetch GEO data') and "
                         "Skill(skill='fetch-geo-processed-matrices'). To get raw reads, "
                         "first resolve the GEO accession to an SRA study with the "
                         "fetch-sequencing-fastq recipe (pysradb), then call this tool "
                         "with the resulting SRP/SRR.")}
    fields = "run_accession,fastq_ftp,sample_title,sample_accession,library_layout,read_count"
    url = (f"https://www.ebi.ac.uk/ena/portal/api/filereport?accession={acc}"
           f"&result=read_run&fields={fields}&format=json")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = _json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        return {"error": f"ENA lookup failed: {e}"}
    runs = []
    for r in data:
        urls = [(u if u.startswith("http") else "https://" + u)
                for u in (r.get("fastq_ftp") or "").split(";") if u]
        runs.append({"run_accession": r.get("run_accession"),
                     "sample_title": r.get("sample_title"),
                     "library_layout": r.get("library_layout"),
                     "read_count": r.get("read_count"),
                     "fastq_urls": urls})
    return {"accession": acc, "n_runs": len(runs), "runs": runs}


def fetch_ensembl(input_: dict, ctx: dict | None = None) -> dict:
    """Fetch a FASTA/GTF from Ensembl, resolving the assembly-versioned filename
    by listing the release directory (P4)."""
    import re
    import urllib.request
    species = (input_.get("species") or "").strip().lower()
    kind = (input_.get("kind") or "cdna").strip()
    release = str(input_.get("release") or "110")
    if not species:
        return {"error": "species is required"}
    if kind in ("cdna", "dna"):
        dir_url = f"https://ftp.ensembl.org/pub/release-{release}/fasta/{species}/{kind}/"
        suffix = ".cdna.all.fa.gz" if kind == "cdna" else ".dna.toplevel.fa.gz"
    elif kind == "gtf":
        dir_url = f"https://ftp.ensembl.org/pub/release-{release}/gtf/{species}/"
        suffix = f".{release}.gtf.gz"
    else:
        return {"error": f"unknown kind '{kind}'"}
    try:
        with urllib.request.urlopen(dir_url, timeout=30) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return {"error": f"Ensembl listing failed: {e}", "dir": dir_url}
    files = re.findall(r'href="([^"]+)"', html)
    match = next((f for f in files if f.endswith(suffix)), None)
    if not match:
        return {"error": f"no '{suffix}' file in {dir_url}", "candidates": files[:20]}
    return fetch_url({"url": dir_url + match, "filename": match}, ctx)
