"""Phase 6.E — discovery / search cluster.

10 tools for finding things — skills, packages (PyPI/bioconda/nf-core
moved earlier; search_bioconda etc. are here), MCP servers, what's in
a package, capability proposal, external fetches (URL, Ensembl, SRA).

Mix of pure and ctx-using tools. ctx-USE tools peek_ctx for things like
project_id (fetch_url stores under DATA_DIR), env state (ensure_capability),
or cancel_token (long-running installs).
"""
from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register the 10 discovery / search / fetch tools on `mcp`."""

    # --- search (all pure, no ctx) ---

    # H6 experiment — env-gated prescriptive docstring. When
    # ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS is set, register the
    # search_skills tool with a directive-shaped description aimed at
    # small open models (Qwen3-30B class) that narrate-instead-of-
    # dispatch after seeing the result. Production (Anthropic) reads
    # the original docstring.
    import os as _os
    if _os.environ.get("ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS"):

        @mcp.tool()
        def search_skills(query: str, limit: int = 8) -> dict:
            """STEP 1 of a MANDATORY two-step recipe-discovery flow.

            Your IMMEDIATE NEXT call after this MUST be:
                Skill(skill="<name from the top result>", args="...")

            Do not stop after this call to summarize what you found.
            Do not switch to run_python before dispatching Skill.
            Do not invent a recipe name — only use names that appear
            in the `skills[*].name` list this returns.

            Pass `query` as a short natural-language phrase like
            "fetch GEO data", "single-cell QC", "differential
            expression". Returns up to `limit` skills ranked by
            relevance — each entry's `invoke_with` field shows the
            exact call shape for step 2."""
            from content.bio.tools import search_skills_tool
            return search_skills_tool({"query": query, "limit": limit})

    else:

        @mcp.tool()
        def search_skills(query: str, limit: int = 8) -> dict:
            """Intent search over the skill (recipe) library.

            Pass a short natural-language `query` like "fetch GEO data",
            "single-cell QC", or "differential expression". Returns up
            to `limit` skills ranked by relevance.

            Returns: {"skills": [{"name": "...", "description": "..."}]}.
            The names in this result are SKILL NAMES, not tool names —
            invoke each one via `Skill(skill="<name>", args="...")`.
            Calling `<name>(...)` directly will fail with "Unknown tool"."""
            from content.bio.tools import search_skills_tool
            return search_skills_tool({"query": query, "limit": limit})

    # ── Experimental: combined fetch_recipe ─────────────────────────
    # Gated by ABA_EXPERIMENTAL_FETCH_RECIPE because it's an open
    # hypothesis test: does a 1-call discover+load tool work better
    # for small models (Qwen3-class) than a 2-call search_skills +
    # Skill chain? Phase 3 of the Qwen3 qualification suite uses this
    # to isolate "chain length is the bottleneck" from other
    # explanations. NOT in production until we've shown the change
    # is net-positive.
    import os as _os
    if _os.environ.get("ABA_EXPERIMENTAL_FETCH_RECIPE"):

        @mcp.tool()
        def fetch_recipe(query: str, args: str = "",
                         aba_ctx_id: str | None = None) -> dict:
            """Find and load a recipe in ONE call. Combines
            `search_skills` + `Skill` into a single dispatch.

            Use this when you know what kind of task you need a recipe
            for but not the exact recipe name. Pass `query` as a short
            natural-language phrase (e.g. "fetch GEO data",
            "single-cell QC", "differential expression"). Optionally
            pass `args` — the string substituted into the recipe's
            `$ARGUMENTS` placeholder.

            Returns the recipe body (markdown with code blocks). Your
            NEXT call should be `run_python` (or `run_r`) with the
            code from the body — this tool only LOADS, it does not
            EXECUTE.

            Prefer this when the user gives you a topic. Use
            `search_skills` + `Skill` instead only when you need to
            compare multiple candidates before picking one."""
            from content.bio.tools import (search_skills_tool,
                                            skill_tool)
            from core.runtime.tool_ctx import peek_ctx
            search_result = search_skills_tool({"query": query, "limit": 1})
            skills = search_result.get("skills") or []
            if not skills:
                return {"error":
                        f"no recipe matches the query {query!r}. "
                        "Try a simpler / different phrasing, or "
                        "call `search_skills` directly to inspect "
                        "candidates."}
            top = skills[0]
            # Delegate to the same skill_tool the Skill MCP wrapper
            # uses — keeps semantics identical.
            body = skill_tool({"skill": top.get("name"), "args": args},
                              peek_ctx(aba_ctx_id))
            # Annotate so the model knows what was chosen + that the
            # next step is run_python/run_r on the body.
            body["_resolved_skill"] = top.get("name")
            body["_resolved_via"]   = "fetch_recipe"
            return body

    @mcp.tool()
    def describe_tool(name: str) -> dict:
        """Return the FULL schema for a tool by name — the verbose
        description plus the input_schema, regardless of how the tool
        was rendered in the catalog prefix for this turn.

        Use this when the catalog shows only a 1-line summary (lean
        catalog mode) and you need the full doc before calling — e.g.
        to confirm parameter names, optional vs required, or the
        nuanced "use this when X / not when Y" guidance.

        Always available regardless of compaction. Returns
        {name, description, input_schema} on success, {error: …}
        on lookup miss."""
        from core.runtime.mcp.gateway import _handles
        from core.runtime.mcp.server_handle import HandleState
        for h in _handles.values():
            if h.state != HandleState.CONNECTED:
                continue
            if not getattr(h, "expose_in_catalog", True):
                continue
            strip = getattr(h, "strip_prefix_in_catalog", False)
            for t in h.tools:
                n = t.raw_name if strip else t.name
                if n == name:
                    return {"name":         n,
                            "description":  t.description or "",
                            "input_schema": t.input_schema}
        return {"error": f"tool {name!r} not found in any connected "
                         "MCP server"}

    @mcp.tool()
    def search_bioconda(query: str) -> dict:
        """Check whether a tool exists on bioconda (awareness only —
        installation goes through ensure_capability)."""
        from content.bio.tools import search_bioconda as _impl
        return _impl({"query": query})

    @mcp.tool()
    def search_nf_core(query: str, limit: int = 5) -> dict:
        """Search the nf-core pipeline registry by name/keyword."""
        from content.bio.tools import search_nf_core as _impl
        return _impl({"query": query, "limit": limit})

    @mcp.tool()
    def search_mcp_registry(query: str, limit: int = 5) -> dict:
        """Search the public MCP server registry — for finding an
        external MCP server to add as a capability."""
        from content.bio.tools import search_mcp_registry as _impl
        return _impl({"query": query, "limit": limit})

    # --- inspect + capability ops (ctx-using) ---

    @mcp.tool()
    def inspect_package(name: str,
                        language: Literal["python", "r"] = "python",
                        object: str | None = None,
                        aba_ctx_id: str | None = None) -> dict:
        """Inspect a package — list submodules / classes / functions,
        OR (with `object`) dump the docstring + signature of a
        specific callable. Uses the project's env."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import inspect_package as _impl
        return _impl({"name": name, "language": language, "object": object},
                     peek_ctx(aba_ctx_id))

    @mcp.tool()
    def ensure_capability(name: str,
                          aba_ctx_id: str | None = None) -> dict:
        """Install / make-ready a named capability (PyPI, conda,
        bioconda, MCP server, …). Long-running for installs — uses
        in_tool_ctx so progress.emit phase lines reach the handler-
        thread sink and stream to the chat as tool_progress events."""
        from core.runtime.tool_ctx import in_tool_ctx
        from content.bio.tools import ensure_capability as _impl
        with in_tool_ctx(aba_ctx_id) as ctx:
            return _impl({"name": name}, ctx)

    @mcp.tool()
    def propose_capability(name: str,
                           archetype: Literal["library", "cli", "r_package",
                                              "mcp_server", "pipeline"] | None = None,
                           channel: str | None = None,
                           source: Literal["pypi", "bioconda", "cran",
                                            "bioconductor", "github"] | None = None,
                           package: str | None = None,
                           library: str | None = None,
                           ref: str | None = None,
                           connection: dict | None = None,
                           url: str | None = None,
                           revision: str | None = None,
                           version: str | None = None,
                           summary: str | None = None,
                           import_name: str | None = None,
                           tags: list[str] | None = None) -> dict:
        """Propose a NEW capability for the catalog (when nothing on
        list_capabilities matches). User reviews before adoption.

        Examples by archetype — copy the matching shape:

          PyPI library (default):
            propose_capability(name='GEOparse', archetype='library',
                               package='GEOparse', import_name='GEOparse')

          CRAN R package:
            propose_capability(name='Seurat', archetype='r_package',
                               source='cran', package='Seurat', library='Seurat')

          Bioconductor:
            propose_capability(name='DESeq2', archetype='r_package',
                               source='bioconductor', package='DESeq2',
                               library='DESeq2')

          GitHub R package — `package` is 'owner/repo'; `ref` is the
          branch / tag / commit (default 'main' if omitted):
            propose_capability(name='pagoda2-devel', archetype='r_package',
                               source='github',
                               package='kharchenkolab/pagoda2',
                               ref='devel', library='pagoda2')

          Conda CLI tool:
            propose_capability(name='samtools', archetype='cli',
                               channel='bioconda', version='1.21')

        If `ensure_capability(name)` returned `status:'candidates'`,
        each suggestion's fields are already shaped for this tool —
        copy them directly."""
        from content.bio.tools import propose_capability_tool
        return propose_capability_tool({
            "name": name, "archetype": archetype, "channel": channel,
            "source": source, "package": package, "library": library,
            "ref": ref, "connection": connection, "url": url,
            "revision": revision, "version": version, "summary": summary,
            "import_name": import_name, "tags": tags,
        })

    # --- external fetches (ctx-using for project_id resolution) ---

    @mcp.tool()
    def fetch_url(url: str, filename: str | None = None,
                  aba_ctx_id: str | None = None) -> dict:
        """Download a URL into DATA_DIR. Registers a dataset only when
        the caller subsequently calls register_dataset."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import fetch_url as _impl
        return _impl({"url": url, "filename": filename},
                     peek_ctx(aba_ctx_id))

    @mcp.tool()
    def fetch_ensembl(species: str,
                      kind: Literal["cdna", "dna", "gtf"],
                      release: str | None = None,
                      aba_ctx_id: str | None = None) -> dict:
        """Fetch Ensembl reference data (genome/GTF/cdna/…) for a
        species into the reference store."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import fetch_ensembl as _impl
        return _impl({"species": species, "kind": kind, "release": release},
                     peek_ctx(aba_ctx_id))

    @mcp.tool()
    def lookup_sra_runinfo(accession: str,
                           aba_ctx_id: str | None = None) -> dict:
        """Look up SRA / ENA run info for an accession before
        downloading. Accepts SRA/ENA accession types ONLY:

          - run:     SRR…, ERR…, DRR…
          - study:   SRP…, ERP…, DRP…
          - project: PRJNA…, PRJEB…, PRJDB…
          - sample:  SRS…, SAMN…

        This tool does NOT handle GEO accessions (GSE…, GSM…). For
        GEO use `search_skills(query="GEO")` then
        `Skill(skill="fetch-geo-processed-matrices", args="<GSE…>")`."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import lookup_sra_runinfo as _impl
        return _impl({"accession": accession}, peek_ctx(aba_ctx_id))
