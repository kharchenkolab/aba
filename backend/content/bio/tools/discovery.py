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


def search_skills_tool(input_: dict) -> dict:
    """Intent search over the skill (recipe) library. The system prompt only
    surfaces a relevant slice of skills; this finds the rest by free-text
    intent ('differential expression', 'cluster single cell data') so the
    agent isn't limited to what happened to be in-prompt this turn. Pass
    `domain` to narrow to one facet (see the domain map in the skills index)."""
    from core.skills import search_skills
    q = (input_.get("query") or "").strip()
    if not q:
        return {"status": "error", "note": "search_skills needs a non-empty `query`."}
    limit = input_.get("limit") or 8
    hits = search_skills(q, limit=int(limit), domain=input_.get("domain"))
    return {"skills": [
        {"name": s.name, "description": s.description,
         "when_to_use": s.when_to_use, "domain": s.domain,
         "capabilities_needed": list(s.capabilities_needed)}
        for s in hits
    ]}


def search_bioconda(input_: dict) -> dict:
    """Check whether a tool exists on bioconda (P2′ awareness only). Returns
    presence + a note that conda materialization is deferred — so the agent can
    answer honestly about CLI tools it cannot yet install (e.g. salmon, STAR)."""
    import json as _json
    import urllib.error
    import urllib.request

    name = (input_.get("query") or input_.get("name") or "").strip().lower()
    if not name:
        return {"error": "query is required"}
    try:
        with urllib.request.urlopen(
            f"https://api.anaconda.org/package/bioconda/{name}", timeout=10
        ) as resp:
            data = _json.loads(resp.read())
        return {
            "found": True, "name": name,
            "latest_version": data.get("latest_version"),
            "summary": data.get("summary"),
            "note": "Available on bioconda and installable on demand: call "
                    "propose_capability(name, archetype='cli') then ensure_capability — "
                    "it installs into the conda tools env and lands on PATH for run_python.",
        }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"found": False, "name": name}
        return {"error": f"bioconda lookup failed ({e.code})"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"bioconda lookup failed: {e}"}


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


def ensure_capability(input_: dict, ctx: dict | None = None) -> dict:
    """Materialize a catalogued capability on demand (P1). Python libraries go
    into the wipeable overlay so the next run_python can import them; non-pip
    CLI tools (conda) are reported as deferred. A long install is cancellable
    (Stop) via the turn's cancel_token, and streams phase progress."""
    name = (input_.get("name") or input_.get("capability") or "").strip()
    if not name:
        return {"error": "name is required"}
    _ct = (ctx or {}).get("cancel_token")
    from core.catalog import resolve_capability
    cap = resolve_capability(name)
    if not cap:
        return {"status": "not_found",
                "note": f"No capability '{name}' in the catalog. Use list_capabilities to search."}
    # Honor the lifecycle: an unapproved (proposed) capability isn't runnable
    # until approved (the 'ask' multi-user gate).
    if cap.get("status") not in (None, "published"):
        return {"status": "awaiting_approval", "name": cap.get("name"),
                "note": f"'{name}' is proposed but not yet approved; it can't be "
                        f"materialized until approval."}
    # Reference catalogue entry (e.g. extracted from biomni): know-how, not a
    # runnable artifact in ABA. Don't pretend to install it.
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
        import importlib.util as _ilu
        _imp0 = cap.get("import_name")
        if _imp0:
            _in_base = _ilu.find_spec(_imp0) is not None
            if _in_base or _overlay_has_import(_imp0):
                _where = "base environment" if _in_base else "materialized overlay"
                return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                        "archetype": cap.get("archetype"), "import_name": _imp0,
                        "note": f"Already available ({_where}); `import {_imp0}` works in run_python."}
        from core.exec import MaterializingExecutor, Provisioning
        try:
            MaterializingExecutor().materialize(Provisioning(pip=list(prov["pip"])), cancel_token=_ct)
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": name, "note": f"materialization failed: {e}"}
        # Authoritatively resolve the import name (seed override → auto-detect),
        # so the agent never guesses `import <pipname>` and thrashes.
        imp = cap.get("import_name") or _detect_import_name(list(prov["pip"]))
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
        from core.exec import MaterializingExecutor, Provisioning
        try:
            MaterializingExecutor().materialize(Provisioning(conda=prov["conda"]), cancel_token=_ct)
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "name": name, "note": f"conda materialization failed: {e}"}
        return {"status": "ready", "name": cap.get("name"), "version": cap.get("version"),
                "archetype": cap.get("archetype"),
                "note": "Installed into the conda tools env; the binary is on PATH — "
                        "invoke it from run_python via subprocess."}
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
        rp = prov["r"]
        from core.exec import r as rexec
        from core import projects
        pid = projects.current() or "default"
        libname = rp.get("library") or rp.get("package") or cap.get("name")
        # The runtime now carries the foundational compiled deps (igraph/irlba/
        # Rcpp*/xml2) as binaries, so GitHub/CRAN installs find them on
        # .libPaths() instead of source-compiling. Heavy frameworks stay on-demand.
        rexec.ensure_r_runtime()
        if rexec.r_has_package(libname, project_id=pid):
            return {"status": "ready", "name": cap.get("name"), "archetype": "r_package",
                    "library": libname,
                    "note": f"Already available — library({libname}) works in run_r."}
        res = rexec.r_install(rp.get("source", "cran"), rp.get("package") or cap.get("name"),
                              project_id=pid, library=libname, ref=rp.get("ref"), cancel_token=_ct)
        if res.get("status") == "ready":
            if res.get("via") == "conda":
                note = (f"Installed as a Bioconductor binary into the shared R environment; "
                        f"use library({libname}) in run_r.")
            else:
                via = " (recompiled from source)" if res.get("source_fallback") else ""
                note = f"Installed into the project R library{via}; use library({libname}) in run_r."
            return {"status": "ready", "name": cap.get("name"), "archetype": "r_package",
                    "library": libname, "note": note}
        # Surface the actionable diagnostic (incl. missing-system-lib hint) so the
        # agent can self-correct (conda-install a dep + retry) or ask the user.
        out = {"status": "error", "name": cap.get("name"), "archetype": "r_package",
               "note": res.get("note") or "R install failed."}
        if res.get("missing_lib"):
            out["missing_lib"] = res["missing_lib"]
        if res.get("diagnostic"):
            out["diagnostic"] = res["diagnostic"]
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
    from core.catalog import resolve_capability, propose_capability as _propose, capability_status

    existing = resolve_capability(name)
    if existing:
        return {"status": "already_available", "name": existing.get("name"),
                "version": existing.get("version"),
                "note": "Already in the catalog — call ensure_capability to install it."}

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
        lib = input_.get("library") or (pkg.split("/")[-1] if r_source == "github" else pkg)
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
