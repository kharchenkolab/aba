"""Phase 6.D — curation cluster.

The agent's highest-volume gestures: promote evidence, draft
findings/claims, attach annotations, archive, register/grow datasets,
open/close runs, manage reference data.

Every handler delegates to the existing bio impl with `peek_ctx(aba_ctx_id)`
so behaviour is byte-identical to the EXECUTORS path. The schemas
declared here mirror TOOL_SCHEMAS (same param names, same required vs
optional). When 6.I prunes TOOL_SCHEMAS and flips expose_in_catalog=True,
THESE annotations become the source of truth — adding a new curation
tool will be one @mcp.tool() block here, no edit anywhere else.

NOTE: `pin_entity` was retired 2026-06-08 (entity-mgmt refactor Phase 1).
It toggled a legacy `pinned` boolean column that no UI surface has read
since task #318 unified "pin" semantics around promote_to_result /
pin_evidence. The actual pin op the agent should use is
`promote_to_result` (figure → new Result) — pin_evidence in the backend.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_curation_tools(mcp: FastMCP) -> None:
    """Register the curation tools on `mcp`. All take ctx for
    thread_id resolution (most write a new entity; the thread it
    belongs to comes from ctx)."""

    @mcp.tool()
    def promote_to_result(entity_id: str, interpretation: str,
                          title: str | None = None,
                          aba_ctx_id: str | None = None) -> dict:
        """Promote a figure to a Result with a written interpretation
        — the deliberate 'this matters' gesture. `entity_id` is the figure."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import promote_to_result_tool
        return promote_to_result_tool(
            {"figure_id": entity_id, "interpretation": interpretation,
             "title": title},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def create_finding(result_ids: list[str], text: str,
                       title: str | None = None,
                       aba_ctx_id: str | None = None) -> dict:
        """Draft a finding citing one or more results as evidence."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import create_finding_tool
        return create_finding_tool(
            {"result_ids": result_ids, "text": text, "title": title},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def create_claim(statement: str,
                     evidence_ids: list[str] | None = None,
                     negative: bool = False,
                     aba_ctx_id: str | None = None) -> dict:
        """Draft a claim — a deliberate position supported by evidence."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import create_claim_tool
        return create_claim_tool(
            {"statement": statement, "evidence_ids": evidence_ids or [],
             "negative": negative},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def annotate_entity(entity_id: str,
                        tags: list[str] | None = None,
                        notes: str | None = None,
                        title: str | None = None,
                        status: str | None = None,
                        aba_ctx_id: str | None = None) -> dict:
        """Update tags/notes/title/status on an entity."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import annotate_entity_tool
        return annotate_entity_tool(
            {"entity_id": entity_id, "tags": tags, "notes": notes,
             "title": title, "status": status},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def archive_entity(entity_id: str,
                       reason: str | None = None,
                       aba_ctx_id: str | None = None) -> dict:
        """Archive an entity (reversible via the UI). The framework
        always asks the user to confirm BEFORE this lands."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import _archive_entity_tool
        return _archive_entity_tool(
            {"entity_id": entity_id, "reason": reason},
            peek_ctx(aba_ctx_id),
        )

    # --- dataset ops ---

    @mcp.tool()
    def register_dataset(title: str,
                         path: str | None = None,
                         paths: list[str] | None = None,
                         summary: str | None = None,
                         source: str | None = None,
                         organism: str | None = None,
                         producing_code: str | None = None,
                         aba_ctx_id: str | None = None) -> dict:
        """Register a file or folder (or list of files) as a Dataset
        entity. Use after a fetch/download so the data joins the
        project's pinnable surface."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import register_dataset_tool
        return register_dataset_tool(
            {"title": title, "path": path, "paths": paths,
             "summary": summary, "source": source, "organism": organism,
             "producing_code": producing_code},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def check_import(entity_id: str, aba_ctx_id: str | None = None) -> dict:
        """Check whether an IMPORTED (by-reference) Run or Dataset is still up to date with the
        external directory it references — did the collaborator re-run, move, or delete it? Fast
        (compares against the baseline captured at import; no re-copy). If it reports STALE, re-run
        import_run on the same path to refresh. Use this to 'maintain' an imported run when the user
        asks whether it's current — do NOT hand-roll a filesystem walk in run_python."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import check_import_tool
        return check_import_tool({"entity_id": entity_id}, peek_ctx(aba_ctx_id))

    @mcp.tool()
    def add_to_dataset(dataset_id: str, paths: list[str],
                       aba_ctx_id: str | None = None) -> dict:
        """Hardlink one or more files into an existing directory-shaped
        dataset's bundle."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import add_to_dataset_tool
        return add_to_dataset_tool(
            {"dataset_id": dataset_id, "paths": paths},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def remove_from_dataset(dataset_id: str, paths: list[str],
                            aba_ctx_id: str | None = None) -> dict:
        """Unlink one or more files from a directory-shaped dataset's
        bundle. Paths must resolve inside the dataset directory."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import remove_from_dataset_tool
        return remove_from_dataset_tool(
            {"dataset_id": dataset_id, "paths": paths},
            peek_ctx(aba_ctx_id),
        )

    # --- run lifecycle ---

    @mcp.tool()
    def open_run(title: str,
                 aba_ctx_id: str | None = None) -> dict:
        """Open an analysis Run so this pipeline's outputs group as one
        unit. Subsequent run_python/run_r figures auto-attach."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import open_run_tool
        return open_run_tool({"title": title}, peek_ctx(aba_ctx_id))

    @mcp.tool()
    def close_run(aba_ctx_id: str | None = None) -> dict:
        """Close the thread's open Run (call when pivoting to unrelated
        work). Empty Runs are discarded on close."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import close_run_tool
        return close_run_tool({}, peek_ctx(aba_ctx_id))

    # --- reference data ---

    @mcp.tool()
    def register_reference(path: str,
                           organism: str | None = None,
                           role: str | None = None,
                           assembly: str | None = None,
                           source: str | None = None,
                           derived_from: str | None = None,
                           version: str | None = None,
                           mode: str = "copy",
                           scope: str | None = None,
                           aba_ctx_id: str | None = None) -> dict:
        """Keep a file/dir as a reusable reference. mode='copy' owns the bytes
        (content-addressed copy); mode='link' adopts a pre-existing cluster path
        in place without copying (for large shared genome/index stores). scope
        ('project'|'group'|'institution') picks the tier it's shared at."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import register_reference_tool
        return register_reference_tool(
            {"path": path, "organism": organism, "role": role,
             "assembly": assembly, "source": source, "derived_from": derived_from,
             "version": version, "mode": mode, "scope": scope},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def find_reference(organism: str | None = None,
                       role: str | None = None,
                       assembly: str | None = None,
                       all: bool = False,
                       aba_ctx_id: str | None = None) -> dict:
        """Find a stored reference by organism/role before fetching."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import find_reference_tool
        return find_reference_tool(
            {"organism": organism, "role": role, "assembly": assembly,
             "all": all},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def fetch_reference(provider: str,
                        organism: str | None = None,
                        assembly: str | None = None,
                        role: str | None = None,
                        accession: str | None = None,
                        aba_ctx_id: str | None = None) -> dict:
        """Fetch a reference / pre-built index from a known provider
        (aws-indexes, ncbi, …) and register it with a re-runnable spec. For
        reference DATA (assemblies, annotations, indices) — distinct from
        lookup_sra_runinfo (sequencing reads). Prefer a pre-built index over
        building one. find_reference first to avoid re-fetching."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import fetch_reference_tool
        return fetch_reference_tool(
            {"provider": provider, "organism": organism, "assembly": assembly,
             "role": role, "accession": accession},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def resolve_reference(reference_id: str | None = None,
                          organism: str | None = None,
                          role: str | None = None,
                          assembly: str | None = None,
                          aba_ctx_id: str | None = None) -> dict:
        """Resolve a stored reference (by id or organism/role/assembly) to a
        local path for use in the current run, and pin the run-lock so the run
        records exactly which reference version it used. Call before reading a
        reference in run_python/run_r."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import resolve_reference_tool
        return resolve_reference_tool(
            {"reference_id": reference_id, "organism": organism,
             "role": role, "assembly": assembly},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def promote_reference(reference_id: str, scope: str,
                          aba_ctx_id: str | None = None) -> dict:
        """Promote a reference up a tier (scope = project|group|institution) so
        it's shared more widely — e.g. a project reference that proves reusable
        lab-wide. Institution is curator-only (permission-gated)."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import promote_reference_tool
        return promote_reference_tool({"reference_id": reference_id, "scope": scope},
                                      peek_ctx(aba_ctx_id))

    @mcp.tool()
    def describe_reference(reference_id: str,
                           aba_ctx_id: str | None = None) -> dict:
        """Inspect a stored reference: facets, content identity, lineage,
        structural path, and how it was acquired."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import describe_reference_tool
        return describe_reference_tool({"reference_id": reference_id},
                                       peek_ctx(aba_ctx_id))
