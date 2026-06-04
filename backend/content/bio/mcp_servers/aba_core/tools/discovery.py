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

    @mcp.tool()
    def search_skills(query: str, domain: str | None = None,
                      limit: int = 8) -> dict:
        """Intent search over the skill (recipe) library — surfaces
        recipes beyond what made it into the slice in the system
        prompt for this turn."""
        from content.bio.tools import search_skills_tool
        return search_skills_tool(
            {"query": query, "domain": domain, "limit": limit})

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
                           source: Literal["cran", "bioconductor", "github"] | None = None,
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
        list_capabilities matches). User reviews before adoption."""
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
        """Look up SRA / GEO / ENA run info for an accession before
        downloading."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import lookup_sra_runinfo as _impl
        return _impl({"accession": accession}, peek_ctx(aba_ctx_id))
