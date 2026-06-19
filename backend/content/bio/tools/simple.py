"""Simple bio tool impls — no `ctx`, no module-level state, pure
(input dict) → result dict. Extracted as part of WU-3-tail
(modularity_audit.md item #3).

The aba_core handlers in
`backend/content/bio/mcp_servers/aba_core/tools/simple.py` delegate to
these via `from content.bio.tools import <name>` (re-exported through
`bio/tools/__init__.py` for back-compat with all import sites)."""

from __future__ import annotations
import json
import re
import urllib.error
import urllib.request
from urllib.parse import quote


def read_memory_tool(input_: dict) -> dict:
    from core.memory import read_memory as _rm, list_memories
    name = (input_.get("name") or "").strip() if isinstance(input_, dict) else ""
    if not name:
        return {"status": "error", "note": "read_memory needs a non-empty `name`."}
    e = _rm(name)
    if e is None:
        avail = [m.name for m in list_memories()]
        return {
            "status": "unknown_memory",
            "note": f"No memory named {name!r}. Available: {', '.join(avail) or '(none)'}.",
        }
    return {
        "status": "ok",
        "name": e.name,
        "type": e.type,
        "description": e.description,
        "body": e.body,
        "caveat": ("This is YOUR own note from a past session — it can be stale or "
                   "wrong (a summary you wrote may have garbled the real numbers). "
                   "Use it to ORIENT (which accession/files, what you tried). Do NOT "
                   "present specific facts from it — sample counts, per-sample "
                   "attributes, demographics, identifiers — as fact without "
                   "re-deriving them from the live source (e.g. re-fetch the GEO "
                   "record). If you answer from memory alone, say it's from a saved "
                   "note and offer to verify."),
    }


def list_capabilities_tool(input_: dict) -> dict:
    """Search the capability catalog (P1). Intent-ranked (BM25 + substring)
    when a query is given, plain tag-filter otherwise. Returns a trimmed
    view for the model."""
    query = input_.get("query")
    tags = input_.get("tags")
    if (query or "").strip():
        from core.catalog import search_capabilities as _search
        caps = _search(query=query, tags=tags)
    else:
        from core.catalog import list_capabilities as _list
        caps = _list(query=None, tags=tags)
    out = []
    for c in caps:
        e = {"name": c.get("name"), "version": c.get("version"),
             "archetype": c.get("archetype"), "summary": c.get("summary"),
             "domain_tags": c.get("domain_tags"), "status": c.get("status")}
        # Mark mined reference entries so the agent doesn't reach for one as
        # a runnable tool — ensure_capability can't install it; it's know-how
        # to read (read_capability → source_ref), or a cue to find a real
        # maintained library.
        if c.get("reference"):
            e["reference"] = True
            e["runnable"] = False
            e["note"] = ("REFERENCE ONLY (mined know-how) — NOT installable via "
                         "ensure_capability. read_capability for its source_ref/idioms, "
                         "or find a runnable library/CLI instead (search_pypi/search_bioconda).")
        out.append(e)
    return {"capabilities": out}


def _pep503(name: str) -> str:
    import re
    return re.sub(r"[-_.]+", "-", name).lower()


def search_pypi(input_: dict) -> dict:
    """Look up a Python package on PyPI (P2′ discovery). Resolves the name (and
    PEP-503 / separator variants) against the PyPI JSON API and returns its
    metadata if it exists. Use this when the agent needs a library that
    list_capabilities didn't find, before proposing it."""
    import json as _json
    import urllib.error
    import urllib.request
    from urllib.parse import quote

    raw = (input_.get("query") or input_.get("name") or "").strip()
    if not raw:
        return {"error": "query is required"}
    # A PyPI package name is a single token; a multi-word query (e.g. "geoparse
    # geo") isn't a package and would put a space in the URL. Take the first token.
    raw = raw.split()[0]
    # Candidate spellings to try, in order; PyPI is case-insensitive and
    # normalizes separators, but trying variants covers user phrasing.
    cands = []
    for c in (raw, _pep503(raw), raw.replace("_", "-"), raw.replace("-", "_")):
        if c and c not in cands:
            cands.append(c)
    for cand in cands:
        try:
            with urllib.request.urlopen(
                f"https://pypi.org/pypi/{quote(cand)}/json", timeout=10
            ) as resp:
                info = (_json.loads(resp.read()).get("info") or {})
            return {
                "found": True,
                "name": info.get("name") or cand,
                "version": info.get("version"),
                "summary": info.get("summary"),
                "requires_python": info.get("requires_python"),
                "home_page": info.get("home_page") or info.get("project_url"),
                "tried": cands,
            }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return {"error": f"PyPI lookup failed ({e.code})", "tried": cands}
        except Exception as e:  # noqa: BLE001
            return {"error": f"PyPI lookup failed: {e}", "tried": cands}
    return {"found": False, "tried": cands,
            "note": "No PyPI package by that name. Check spelling, or it may be a "
                    "non-Python CLI tool (try search_bioconda)."}
