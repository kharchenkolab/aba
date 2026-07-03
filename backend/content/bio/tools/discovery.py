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
            from core.exec.r import project_r_lib
            ov["r_project_lib"] = str(project_r_lib(pid)) if pid else None
        return {"status": "ok", "scope": "overview", "language": "r" if is_r else "python",
                "tiers": ov}

    if is_r:
        # Direct Rscript (like r_has_package) so it works without a kernel ctx
        # and respects the project's .libPaths(). requireNamespace = real load;
        # packageVersion + find.package give version/tier.
        from core.exec.r import _run_rscript, project_r_lib, libpaths_expr
        _lib = libpaths_expr(pid)
        expr = ((_lib + "; " if _lib else "")
                + f"ok <- requireNamespace({name!r}, quietly=TRUE); "
                + f"cat('ABA_LOADS=', isTRUE(ok), '\\n', sep=''); "
                + f"if (isTRUE(ok)) {{ cat('ABA_VER=', as.character(packageVersion({name!r})), '\\n', sep=''); "
                + f"cat('ABA_LOC=', find.package({name!r}), '\\n', sep='') }}")
        try:
            proc = _run_rscript(expr, timeout_s=120)
            out = (getattr(proc, "stdout", "") or "")
            err = (getattr(proc, "stderr", "") or "")
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": name, "language": "r",
                    "loads": False, "error": str(e)[:400]}

        def _pick(key):
            for ln in out.splitlines():
                if ln.startswith(key):
                    return ln[len(key):].strip()
            return None
        loads = (_pick("ABA_LOADS=") == "TRUE")
        loc = _pick("ABA_LOC=")
        tier = ("project-lib" if (loc and pid and str(project_r_lib(pid)) in loc) else
                ("base" if loads else "unknown"))
        return {"status": "ok", "name": name, "language": "r", "loads": loads,
                "version": _pick("ABA_VER="), "location": loc, "tier": tier,
                "error": None if loads else (err or out)[-600:]}

    from core.exec.env_integrity import python_package_status
    st = python_package_status(name, project_id=pid)
    return {"status": "ok", "language": "python", **st}


def make_isolated_env(input_: dict, ctx: dict | None = None) -> dict:
    """Create/refresh an ISOLATED environment you OWN (Python venv, or — with
    language='r' — a standalone R library) and install packages into it with FULL
    version control. USE THIS when a package conflicts with the base (a different
    numpy, tensorflow, an ABI-incompatible wheel) or you need to resolve a
    dependency conflict your own way — the shared base is never touched. Run code
    in it with run_in_isolated_env. (Note for R: a *project* R install already
    overrides the base via .libPaths, so reach for this only for a fully
    project-independent / one-off conflicting lib.) Returns {status, name,
    language, engine, installed, verified, error}."""
    from core.exec import isolated_env as iso
    name = (input_.get("name") or "").strip()
    if not name:
        return {"status": "error", "note": "make_isolated_env needs a `name`."}
    if iso.is_reserved_name(name):
        return {"status": "error", "name": name,
                "note": f"'{name}' is reserved (default/base/shared/project) — it denotes "
                        "the normal environment, not an isolated one. Pick another name."}
    is_r = (input_.get("language") or "python").strip().lower() in ("r", "rlang")
    label = "R" if is_r else "Python"
    lang = "r" if is_r else "python"
    packages = list(input_.get("packages") or [])
    try:
        info = iso.r_create_env(name) if is_r else iso.create_env(name)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "name": name, "note": f"could not create env: {e}"}
    engine = info["engine"]
    if not packages:
        iso.capture_env_spec(name, language=lang, packages=[])   # §11.6 spec/lock
        _run = "run_r" if is_r else "run_python"
        return {"status": "ok", "name": name, "language": lang, "engine": engine,
                "note": f"Isolated {label} env {name!r} ready ({engine}); install packages "
                        f"or run code in it with {_run}(env={name!r}, code=…)."}
    res = (iso.r_install_into(name, packages) if is_r
           else iso.install_into(name, packages, verify_imports=input_.get("verify_imports")))
    if not res["ok"]:
        return {"status": "error", "name": name, "language": lang, "engine": engine,
                "installed": packages, "error": res.get("error"),
                "note": "Isolated env created, but the install failed — see error."}
    iso.capture_env_spec(name, language=lang, packages=packages)   # §11.6 spec/lock
    _run = "run_r" if is_r else "run_python"
    return {"status": "ok", "name": name, "language": lang, "engine": engine,
            "installed": res["installed"], "verified": res.get("verified"),
            "note": f"Isolated {label} env {name!r} ready ({engine}); run code in it with "
                    f"{_run}(env={name!r}, code=…)."}


def run_in_isolated_env(input_: dict, ctx: dict | None = None) -> dict:
    """Run code inside an isolated env created by make_isolated_env — your sandbox
    for conflict resolution / troubleshooting. `language` = python (default) | r.
    Returns {status, language, stdout, stderr}."""
    from core.exec import isolated_env as iso
    name = (input_.get("name") or "").strip()
    code = input_.get("code") or ""
    if not name or not code:
        return {"status": "error", "note": "run_in_isolated_env needs `name` and `code`."}
    is_r = (input_.get("language") or "python").strip().lower() in ("r", "rlang")
    ts = int(input_.get("timeout_s") or 600)
    r = iso.r_run_in(name, code, timeout_s=ts) if is_r else iso.run_in(name, code, timeout_s=ts)
    return {"status": "ok" if r["ok"] else "error", "name": name,
            "language": "r" if is_r else "python", "stdout": r["stdout"], "stderr": r["stderr"]}


def set_active_env(input_: dict, ctx: dict | None = None) -> dict:
    """§11.2 — set the project's ACTIVE python env; bare run_python uses it until
    changed. name='default' resets to the normal served stack. (Python only — R's
    per-project lib already overrides the base, so run_r has no active pointer.)"""
    from core.exec import isolated_env as iso
    from core import projects
    name = (input_.get("name") or "").strip()
    if not name:
        return {"status": "error", "note": "set_active_env needs a `name` (or 'default')."}
    pid = projects.current()
    if name.lower() != "default" and name not in iso.list_envs():
        return {"status": "error", "name": name,
                "note": f"No isolated python env '{name}'. Create it with make_isolated_env, "
                        "or pass 'default' to use the normal environment."}
    iso.set_active_env(pid, name, "python")
    if name.lower() == "default":
        return {"status": "ok", "active_python_env": "default",
                "note": "Bare run_python now uses the default served stack."}
    return {"status": "ok", "active_python_env": name,
            "note": f"Bare run_python now runs in '{name}'. Use env='default' for a one-off "
                    f"in the normal stack, or set_active_env('default') to switch back."}


def _is_constraint_conflict(msg: str) -> bool:
    """Does a pip failure look like UNSAT-against-the-base (a version/constraint
    conflict the pinned base forbids), vs a transient/typo/network error?
    Conservative — only the clear pip resolver-conflict signals, so we never
    mis-route a fat-fingered package name into isolation."""
    m = (msg or "").lower()
    return any(s in m for s in (
        "resolutionimpossible",
        "conflicting dependencies",
        "the conflict is caused by",
    ))


def _auto_isolate(name: str, pip_specs: list[str], cap: dict) -> dict:
    """UNSAT against the base → install into an ISOLATED env the agent owns
    (base untouched). The capability is NOT importable in run_python; the agent
    runs its code via run_in_isolated_env."""
    from core.exec import isolated_env as iso
    env_name = f"cap-{name}"
    imp = cap.get("import_name")
    try:
        iso.create_env(env_name)
        res = iso.install_into(env_name, pip_specs, verify_imports=[imp] if imp else None)
    except Exception as ie:  # noqa: BLE001
        return {"status": "error", "name": name,
                "note": f"conflicts with the base, and the isolated-env fallback failed: {ie}"}
    if not res["ok"]:
        return {"status": "error", "name": name, "isolated_env": env_name,
                "note": "conflicts with the base AND the isolated install also failed — see error.",
                "error": res.get("error")}
    return {"status": "ready_isolated", "name": name, "isolated_env": env_name,
            "installed": res["installed"], "verified": res.get("verified"),
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
    import os
    return os.environ.get("ABA_MCP_REGISTRY_URL", _DEFAULT_MCP_REGISTRY_URL)


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
    try:
        from core.exec.materialize import pylib_paths
        dirs = [str(p) for p in pylib_paths()]
    except Exception:  # noqa: BLE001
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


def _overlay_has_import(import_name: str) -> bool:
    """Is import_name already materialized in the pip overlay? Faithful to
    run_python (which appends the overlay to sys.path) but thread-safe — probes
    the overlay dir directly via PathFinder, never mutating sys.path."""
    if not import_name:
        return False
    try:
        from core.exec.materialize import pylib_paths
        from importlib.machinery import PathFinder
        import importlib
        importlib.invalidate_caches()   # overlay dir may have appeared post-startup
        search = [str(p) for p in pylib_paths()]
        return PathFinder.find_spec(import_name, search) is not None
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


def ensure_capability(input_: dict, ctx: dict | None = None) -> dict:
    """Materialize a catalogued capability on demand (P1). Python libraries go
    into the wipeable overlay so the next run_python can import them; non-pip
    CLI tools (conda) are reported as deferred. A long install is cancellable
    (Stop) via the turn's cancel_token, and streams phase progress."""
    name = (input_.get("name") or input_.get("capability") or "").strip()
    if not name:
        return {"error": "name is required"}
    _ct = (ctx or {}).get("cancel_token")
    # Cluster module provider (prefer:first, job-path scope): does a cluster module
    # satisfy this tool by exact name? resolve() matches only an exact module name,
    # so pip libraries never match and fall through. Record it now so the project's
    # background Slurm jobs `module load` it; the branches below read `_mod` (conda
    # still builds for in-process; an uncatalogued tool like cellranger is
    # module-only — see the not_found path). No-op off a cluster.
    _mod = None
    try:
        from core.exec import modules as _modprov
        if _modprov.modules_active():
            _mod = _modprov.resolve(name)
            if _mod:
                from core import projects as _projects
                _modprov.record_project_module(_projects.current(), _mod)
                # B: make the tool usable IN-PROCESS now — prepend the module's
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
    from core.catalog import resolve_capability
    cap = resolve_capability(name)
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
        # E-1: parallel-search external registries for an exact-name match
        # instead of pointing at list_capabilities (which would also be
        # empty for an uncatalogued name). Returns suggestions shaped for
        # direct copy into propose_capability.
        suggestions = _search_external_for_name(name)
        if suggestions:
            return {
                "status": "candidates",
                "name": name,
                "suggestions": suggestions,
                "note": (
                    "Not in the catalog yet, but matched on external "
                    "registries. Pick one and call propose_capability "
                    "with the matching fields (source/archetype/package "
                    "etc. on each suggestion are already shaped for it), "
                    "then re-call ensure_capability."),
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
    from core.runtime import progress
    progress.emit(f"Materializing '{cap.get('name')}'…", phase="ensure")
    prov = cap.get("provisioning") or {}
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
        from core.exec.env_integrity import verify_python_imports
        _imp0 = cap.get("import_name")
        if _imp0:
            _ok, _ = verify_python_imports([_imp0])
            if _ok:
                return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                        "archetype": cap.get("archetype"), "import_name": _imp0,
                        "note": f"Already available; `import {_imp0}` works in run_python."}
        from core.exec import MaterializingExecutor, Provisioning
        try:
            from core import projects as _projects
            MaterializingExecutor().materialize(
                Provisioning(pip=list(prov["pip"])),
                scope=str(cap.get("scope", "system")),
                cancel_token=_ct, project_id=_projects.current())
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
            _ok, _detail = verify_python_imports([imp])
            if not _ok:
                return {"status": "error", "name": name, "import_name": imp,
                        "note": (f"Installed, but `import {imp}` fails to load — likely an ABI "
                                 f"mismatch (built against a different numpy), a partial install, "
                                 f"or a missing system library. NOT marking ready."),
                        "detail": _detail}
        note = "Installed into the materialized-library overlay; importable from run_python now."
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
        return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                "archetype": cap.get("archetype"), "import_name": imp, "note": note}
    if prov.get("conda"):
        # `_mod` (resolved + recorded up top) means a cluster module also covers this
        # CLI tool — it's loaded in the project's background jobs. We still build conda
        # for in-process use; a conda failure is non-fatal when `_mod` covers it.
        from core.exec import MaterializingExecutor, Provisioning
        try:
            MaterializingExecutor().materialize(Provisioning(conda=prov["conda"]), cancel_token=_ct)
        except Exception as e:  # noqa: BLE001
            if _mod:
                return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                        "archetype": cap.get("archetype"), "module": _mod,
                        "note": f"Provided by cluster module '{_mod}' (loaded in background Slurm "
                                f"jobs); the conda install isn't needed there and failed: {e}"}
            return {"status": "error", "name": name, "note": f"conda materialization failed: {e}"}
        _note = ("Installed into the conda tools env; the binary is on PATH — "
                 "invoke it from run_python via subprocess.")
        if _mod:
            _note += f" Background Slurm jobs also load cluster module '{_mod}'."
        return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                "archetype": cap.get("archetype"), "note": _note, "module": _mod}
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
            return {"status": "ready", "name": cap.get("name"), "archetype": "mcp_server",
                    "tools": tools,
                    "note": f"Connected; {len(tools)} tool(s) now callable: "
                            f"{', '.join(tools[:8])}{'…' if len(tools) > 8 else ''}."}
        return {"status": "error", "name": cap.get("name"), "archetype": "mcp_server",
                "note": f"Could not connect MCP server: {res.get('note')}"}
    if prov.get("pipeline"):
        pl = prov["pipeline"]
        engine = (pl.get("engine") or "nextflow").lower()
        if engine != "nextflow":
            return {"status": "deferred", "name": cap.get("name"), "archetype": "pipeline",
                    "note": f"Pipeline engine '{engine}' isn't wired yet (only nextflow)."}
        from core.exec import MaterializingExecutor, Provisioning
        try:
            MaterializingExecutor().materialize(Provisioning(conda={"channel": "bioconda", "spec": "nextflow"}), cancel_token=_ct)
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": cap.get("name"), "archetype": "pipeline",
                    "note": f"Could not install nextflow: {e}"}
        ref = pl.get("nf_core") or cap.get("name")
        return {"status": "ready", "name": cap.get("name"), "archetype": "pipeline",
                "note": f"nextflow installed and on PATH. Run this pipeline with "
                        f"run_nextflow(pipeline='{ref}', profile='test', ...). "
                        f"(Large runs will route to HPC/remote later — local only for now.)"}
    if prov.get("r"):
        # R package (r_provisioning.md): already on the library path → ready;
        # else a project-scoped native install (CRAN/Bioconductor/GitHub). The
        # shared base is never mutated here — only curation grows it.
        rp = dict(prov["r"])
        # Per-install overrides (P5 fix #2): the caller can pin a different git
        # ref / source / package for THIS install without re-cataloguing — e.g.
        # ensure_capability(name='pagoda2', source='github',
        # package='kharchenkolab/pagoda2', ref='devel') installs from a branch
        # even though the catalog entry is the CRAN release. Transient: the
        # catalog row is not mutated.
        for _k in ("ref", "source", "package"):
            if input_.get(_k):
                rp[_k] = input_[_k]
        from core.exec import r as rexec
        from core import projects
        pid = projects.current() or "default"
        _src = rp.get("source", "cran")
        _pkg = rp.get("package") or cap.get("name")
        libname = rp.get("library") or (
            _pkg.split("/")[-1] if _src == "github"
            else (_pkg[2:] if _src == "conda" and _pkg.startswith("r-") else _pkg))
        # The runtime now carries the foundational compiled deps (igraph/irlba/
        # Rcpp*/xml2) as binaries, so GitHub/CRAN installs find them on
        # .libPaths() instead of source-compiling. Heavy frameworks stay on-demand.
        rexec.ensure_r_runtime()
        # Version-aware presence check (was presence-only — the sccore-upgrade
        # trap): a min-version requirement, an explicit force, or an install
        # override (ref/source/package — "I want THIS build") all mean "already
        # installed" is NOT enough, so reinstall instead of short-circuiting to
        # "ready" while leaving a stale version.
        min_version = (str(input_.get("min_version") or rp.get("min_version") or "").strip() or None)
        force = bool(input_.get("force"))
        override = any(input_.get(_k) for _k in ("ref", "source", "package"))
        installed_ver = rexec.r_package_version(libname, project_id=pid)
        satisfied = installed_ver is not None and (
            not min_version or rexec.version_ge(installed_ver, min_version))
        if satisfied and not force and not override:
            return {"status": "ready", "name": cap.get("name"), "archetype": "r_package",
                    "library": libname, "version": installed_ver,
                    "note": f"Already available — library({libname}) {installed_ver} works in run_r."}
        # (Re)install. force=TRUE so install_github replaces an already-present-but-
        # stale build; required whenever we're upgrading or honoring an override.
        do_force = force or override or (installed_ver is not None and min_version is not None)
        res = rexec.r_install(_src, _pkg, project_id=pid, library=libname,
                              ref=rp.get("ref"), force=do_force, cancel_token=_ct)
        if res.get("status") == "ready":
            new_ver = rexec.r_package_version(libname, project_id=pid) or res.get("version")
            # If the OLD version is loaded in the running R kernel it can't be
            # swapped in place — unload it so the next library() loads the new one
            # (falls back to restart_kernel if another loaded namespace pins it).
            unloaded = rexec.r_unload_namespace(libname, (ctx or {}).get("thread_id"))
            if res.get("source") == "conda" or res.get("via") == "conda":
                where = "into the shared R environment (Bioconductor binary)"
            else:
                where = "into the project R library" + (
                    " (recompiled from source)" if res.get("source_fallback") else "")
            note = (f"Installed {libname}{(' ' + new_ver) if new_ver else ''} {where}; "
                    f"use library({libname}) in run_r.")
            if not unloaded and installed_ver and new_ver and installed_ver != new_ver:
                note += (" A prior load may be cached in the R session — restart_kernel "
                         "so the new version takes effect.")
            return {"status": "ready", "name": cap.get("name"), "archetype": "r_package",
                    "library": libname, "version": new_ver, "note": note}
        # Error → surface the actionable diagnostic (missing-system-lib hint) AND,
        # crucially, any unmet VERSION requirement so the agent upgrades the dep in
        # ONE step instead of inferring the whole dance.
        out = {"status": "error", "name": cap.get("name"), "archetype": "r_package",
               "note": res.get("note") or "R install failed."}
        if res.get("missing_lib"):
            out["missing_lib"] = res["missing_lib"]
        if res.get("diagnostic"):
            out["diagnostic"] = res["diagnostic"]
        req = rexec.parse_version_requirement(
            (res.get("note") or "") + "\n" + str(res.get("diagnostic") or ""))
        if req:
            out["requires"] = req
            out["note"] = (f"{out['note']} — needs {req['package']} >= {req['min_version']}. "
                           f"Upgrade it first: ensure_capability(name={req['package']!r}, "
                           f"min_version={req['min_version']!r}, force=true), then retry. "
                           f"(The reinstall unloads the stale namespace; if another loaded "
                           f"package pins it, restart_kernel.)")
        return out
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
    import os as _os
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
    mode = _os.environ.get("ABA_CAPABILITY_APPROVAL", "auto")
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
