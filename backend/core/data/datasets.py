"""Dataset byte-plane mechanism (misc/datasets2.md v2, weft-reviewed).

Datasets are REFERENCES first; bytes move only by decision. Two orthogonal
facets, acquired at different moments, both carried in the entity metadata:

  * the DURABLE HOME — a real filesystem location `{site, path}` recorded at
    registration with a cheap stat-level fingerprint (weft `data_fingerprint`;
    no ingest, no TB read). The sibling of external_ref.py, extended to weft
    sites.
  * the CONTENT IDENTITY — a weft DataRef, minted LAZILY at first
    computational use (weft doctrine: "identity lazily"): reference-in-place
    (`data_register(ingest=False)`) for site paths — zero copy, symlink
    staging, stat-fence — and a normal CAS ingest only where custody is
    physically required (jobdir-produced bytes before the sweep, URL fetches).

Drift discipline (the memo trap found live 2026-07-17): an identical resubmit
memo-hits BEFORE staging, so the stat-fence never runs — a drifted home would
silently serve the old result. Therefore `ensure_ref` re-fingerprints the
home on every mint/reuse and reports drift; callers surface it (new dataset
revision), never ignore it.

This module is MECHANISM (domain-neutral, no entity/graph writes). The
`register_dataset` tool layers aba's semantics (entity mint, source-key
dedup, drift policy) on top. All weft calls go through the adapter's ports;
callers run on worker threads (sync_call/run_sync — never the event loop).
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Optional

URL_SCHEMES = ("http://", "https://", "s3://", "gs://", "azure://")

# fetch-to-controller guardrail: above this, refuse and suggest running where
# the data lives (misc/datasets2.md §5). Deliberately NOT a setting.
FETCH_GUARDRAIL_BYTES = 2 * 1024**3

_FP_HASH_UNDER = 0          # stat-only by default (sampling is the caller's call)
_FP_MAX_ENTRIES = 50_000
_DESCRIPTOR_TOP = 12


def _comp():
    from core.compute.adapter import get_compute
    return get_compute()


def is_url(source: str) -> bool:
    return any(source.startswith(s) for s in URL_SCHEMES)


def source_key(source: str, site: Optional[str] = None) -> str:
    """The semantic dedup key: URLs are global; paths are per-site."""
    if is_url(source):
        return source
    return f"{site or 'local'}:{os.path.normpath(source)}"


def resolve_site_for_path(abspath: str) -> str:
    """Which site a path belongs to: longest prefix match against each
    site's declared durable storage and its weft root's parent; else local.
    (On shared-fs deployments the same path is visible everywhere, so
    'local' is always a safe answer there.)"""
    from core.compute import sites_config
    best, best_len = "local", -1
    for entry in sites_config.list_declared_sites():
        name = entry.get("name") or ""
        prefixes = [e.get("path") for e in
                    ((entry.get("aba") or {}).get("storage") or [])]
        root = (entry.get("config") or {}).get("root")
        if root:
            prefixes.append(os.path.dirname(str(root)))
        for p in prefixes:
            if not p:
                continue
            p = os.path.normpath(str(p))
            if (abspath == p or abspath.startswith(p + os.sep)) \
                    and len(p) > best_len:
                best, best_len = name, len(p)
    return best


# ── fingerprints (weft data_fingerprint → external_ref-compatible digest) ────

def fingerprint_site_path(path: str, site: str) -> dict:
    """Stat-level fingerprint of a path ON A SITE, normalized to the
    external_ref shape: {exists, n_files, total_bytes, digest, truncated,
    top}. `top` (first-level names) feeds the descriptor."""
    from core.compute.errors import ComputeError
    try:
        fp = _comp().sync_call("data_fingerprint", path, site,
                               hash_under=_FP_HASH_UNDER,
                               max_entries=_FP_MAX_ENTRIES)
    except ComputeError as e:
        if "missing" in e.code or "not" in e.detail.lower():
            return {"exists": False}
        raise
    entries = fp.get("entries") or []
    if not entries and not fp.get("bytes"):
        return {"exists": False}
    lines = sorted(f"{e['path']}\t{e['bytes']}\t{e.get('mtime', 0)}"
                   for e in entries)
    digest = hashlib.sha1("\n".join(lines).encode()).hexdigest()
    top = sorted({e["path"].split("/", 1)[0] for e in entries})[:_DESCRIPTOR_TOP]
    return {"exists": True,
            "n_files": len(entries),
            "total_bytes": sum(e.get("bytes", 0) for e in entries),
            "digest": digest,
            "truncated": bool(fp.get("truncated")),
            "top": top}


def descriptor_from(fp: dict) -> dict:
    """What the controller shows for data it never holds (§5)."""
    return {"total_bytes": fp.get("total_bytes"),
            "n_files": fp.get("n_files"),
            "top": fp.get("top") or [],
            "truncated": bool(fp.get("truncated"))}


# ── registration-time record ─────────────────────────────────────────────────

def register_source(source: str, *, site: Optional[str] = None,
                    eager_ref_max_bytes: int = 0) -> dict:
    """The byte-plane record for a new dataset entity. Returns metadata
    fields (the caller merges them into the entity):

      {source_kind, source_key, home?, fingerprint?, descriptor,
       origin_class, ref?}

    * URL → fetched NOW into the target site's CAS (weft never
      short-circuits URL fetches; the semantic pre-check is the caller's
      job BEFORE calling this). origin `url` — evictable, refetchable.
    * site/local path → durable-home record: fingerprint + descriptor,
      NO ingest, NO ref (content identity mints at first use). Set
      `eager_ref_max_bytes` > 0 to mint the reference-in-place ref
      immediately for small data (one read pass).
    """
    comp = _comp()
    if is_url(source):
        r = comp.sync_call("data_register", source, site=site or "local")
        return {"source_kind": "url", "source_key": source_key(source),
                "origin_class": "url", "ref": r["ref"],
                "home": None,
                "descriptor": {"total_bytes": r.get("bytes"),
                               "n_files": r.get("files"),
                               "top": [], "truncated": False}}
    abspath = os.path.normpath(source)
    site = site or resolve_site_for_path(abspath)
    fp = fingerprint_site_path(abspath, site)
    if not fp.get("exists"):
        return {"source_kind": "site_path", "source_key":
                source_key(abspath, site), "origin_class": "path",
                "home": {"site": site, "path": abspath},
                "fingerprint": {"exists": False}, "descriptor": {},
                "ref": None}
    out = {"source_kind": "site_path",
           "source_key": source_key(abspath, site),
           "origin_class": "path",
           "home": {"site": site, "path": abspath},
           "fingerprint": fp,
           "descriptor": descriptor_from(fp),
           "ref": None}
    if eager_ref_max_bytes and (fp.get("total_bytes") or 0) <= eager_ref_max_bytes:
        r = comp.sync_call("data_register", abspath, site=site, ingest=False)
        out["ref"] = r["ref"]
    return out


def ingest_produced(abspath: str, *, site: str = "local") -> dict:
    """Jobdir-produced bytes: CAS ingest NOW (the jobdir is swept with the
    kernel — custody is physically required). origin `run`: recomputable
    while the producer is re-runnable; the CAS copy is the working copy."""
    comp = _comp()
    if site == "local":
        r = comp.sync_call("data_register", abspath)
    else:
        r = comp.sync_call("data_register", abspath, site=site)
    return {"source_kind": "produced",
            "source_key": source_key(abspath, site),
            "origin_class": "run", "ref": r["ref"], "home": None,
            "descriptor": {"total_bytes": r.get("bytes"),
                           "n_files": r.get("files"),
                           "top": [], "truncated": False}}


# ── first use / drift ────────────────────────────────────────────────────────

def revalidate(meta: dict) -> dict:
    """Compare the durable home now vs the recorded fingerprint.
    → {state: unchanged|drifted|missing|no_home, fingerprint?}"""
    home = meta.get("home") or {}
    if not home.get("path"):
        return {"state": "no_home"}
    fp = fingerprint_site_path(home["path"], home.get("site") or "local")
    if not fp.get("exists"):
        return {"state": "missing", "fingerprint": fp}
    old = (meta.get("fingerprint") or {}).get("digest")
    state = "unchanged" if (old and fp["digest"] == old) else "drifted"
    return {"state": state, "fingerprint": fp}


def ensure_ref(meta: dict) -> dict:
    """Content identity at first use (weft's own pattern: called right
    before the task_submit that names the ref). For durable-home datasets
    ALWAYS re-fingerprints first — memoization hits before staging, so
    this is the only fence against a drifted home silently serving a
    stale memoized result.

    → {ref?, state: ok|drifted|missing|no_source, fingerprint?}"""
    home = meta.get("home") or {}
    if not home.get("path"):
        # CAS-backed (url/produced): the ref is already the identity
        return {"ref": meta.get("ref"),
                "state": "ok" if meta.get("ref") else "no_source"}
    check = revalidate(meta)
    if check["state"] in ("drifted", "missing"):
        return {"ref": meta.get("ref"), "state": check["state"],
                "fingerprint": check.get("fingerprint")}
    if meta.get("ref"):
        return {"ref": meta["ref"], "state": "ok"}
    r = _comp().sync_call("data_register", home["path"],
                          site=home.get("site") or "local", ingest=False)
    return {"ref": r["ref"], "state": "ok"}


# ── serving / transfer guardrail ─────────────────────────────────────────────

def fetch_check(meta: dict, *, limit: int = FETCH_GUARDRAIL_BYTES) -> dict:
    """The controller is a viewer, not a way-station: refuse oversized
    fetches with the placement suggestion instead (§5)."""
    size = (meta.get("descriptor") or {}).get("total_bytes")
    if size is not None and size > limit:
        home = meta.get("home") or {}
        return {"ok": False, "total_bytes": size, "limit": limit,
                "suggestion": ("run the analysis on "
                               f"{home.get('site', 'the site holding the data')} "
                               "instead of transferring "
                               f"{size / 1e9:.1f} GB here")}
    return {"ok": True, "total_bytes": size}


def explain_data_error(err: Any) -> Optional[str]:
    """Translate a weft data-plane error (ComputeError or its payload) into a
    plain, agent-facing sentence — never a raw weft string at the user
    (misc/datasets2.md §S3). Returns None for errors that aren't data-plane.

    Staging is async, so an external-home drift lands as a JOB FAILURE with
    data.verify_failed(source=external-home); data.missing means the home is
    gone with locations exhausted."""
    code = getattr(err, "code", None)
    hints = getattr(err, "hints", None)
    detail = getattr(err, "detail", "")
    if code is None and isinstance(err, dict):
        code = err.get("error") or err.get("code")
        hints = err.get("hints")
        detail = err.get("detail", "")
    hints = hints or {}
    if code == "data.verify_failed" and hints.get("source") == "external-home":
        home = hints.get("home") or "the registered path"
        site = hints.get("site") or "its machine"
        rec, obs = hints.get("recorded") or {}, hints.get("observed") or {}
        delta = ""
        if rec.get("bytes") is not None and obs.get("bytes") is not None:
            delta = f" (was {rec['bytes']} bytes, now {obs['bytes']})"
        return (f"The dataset at {home} on {site} has changed since it was "
                f"registered{delta}. Re-register it — that mints a new "
                f"revision — before using it; results memoized against the "
                f"old content won't (and shouldn't) be reused.")
    if code == "data.verify_failed":
        return ("A dataset failed its content check after transfer "
                f"({detail}). It may be corrupted in transit — retry, or "
                "re-register the source.")
    if code == "retain.no_durable":
        return ("This machine has no safe storage declared, so results can't "
                "be kept on it. Declare durable storage on its machine card "
                "(Settings → Compute), or ship the results here explicitly.")
    if code == "data.missing":
        return (f"A dataset's stored bytes are gone ({detail}). If its durable "
                "home still exists, re-register it; if it was produced by a "
                "run, re-run that step to recompute it.")
    return None


def fetch(meta: dict, to_path: str, *,
          limit: int = FETCH_GUARDRAIL_BYTES, force: bool = False) -> dict:
    """Stage a dataset's bytes to a local path (guardrailed). Mints the ref
    first if needed; drift/missing surface instead of fetching stale."""
    if not force:
        chk = fetch_check(meta, limit=limit)
        if not chk["ok"]:
            return {"error": "fetch_guardrail", **chk}
    ident = ensure_ref(meta)
    if ident["state"] != "ok" or not ident.get("ref"):
        return {"error": f"source_{ident['state']}", **ident}
    r = _comp().sync_call("data_fetch", ident["ref"], to_path)
    return {"ok": True, "ref": ident["ref"], **{k: r[k] for k in ("path",)
                                                if k in r}}
