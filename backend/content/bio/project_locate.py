"""THE project-wide name→file door: say the name, get labeled answers.

Contract (the whole agent-facing model, one sentence): refer to files by the
name your code used; the platform finds them. Everything else here is
platform-internal — the walk over the custody chain:

    T1 live sandboxes   — every active kernel, local (jobdir walk) AND remote
                          (inventory match — metadata the scrape already holds;
                          a lookup NEVER moves bytes)
    T2 run manifests    — produced[] across recent execs, INCLUDING link-only
                          rows (over-cap outputs that never came home), with
                          store copies resolved to their served path
    T3 user/scratch     — the data dir + thread scratch trees

Honesty rules (each guarded):
  - results NAME their bounds — a bounded search that doesn't declare its
    bound reads as exhaustive (the silent-truncation class);
  - an unreachable site makes results there UNKNOWN, never absent — and a
    manifest-known file on a dead site is still LISTED, marked unavailable;
  - collisions return labeled candidates (run provenance), never a silent
    newest-wins;
  - every hit says which tier answered and what opening costs.
"""
from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".exec",
              "envs", ".cache", ".pytest_cache"}
_RECENT_EXECS = 40          # manifest-tier bound — DECLARED in every result


def _mtime_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# Durability is an EXPLICIT per-tier fact, defaulting EPHEMERAL: the original
# marking string-matched tier names ("sandbox"/"scratch"), so a renamed or
# added tier silently defaulted to durable — a durability claim made by
# silence, pointing the wrong way (the F2 class). New tiers must be entered
# here deliberately; an unknown tier is treated as swept-any-time.
_TIER_DURABILITY = {"live sandbox": "ephemeral", "work scratch": "ephemeral",
                    "run output": "durable", "user data": "durable"}


def _walk_match(root: Path, pattern: str, hits: list, tier: str,
                cap: int) -> None:
    if not root or not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            fp = Path(dirpath) / name
            # Tier parity: remote and manifest tiers match basename OR rel
            # path — the local walks must too, or 'sub/x.csv' resolves a
            # manifest row and a remote file but never the same file on disk.
            rel = str(fp.relative_to(root)).replace(os.sep, "/")
            if not (fnmatch.fnmatchcase(name, pattern)
                    or fnmatch.fnmatchcase(rel, pattern)):
                continue
            try:
                st = fp.stat()
            except OSError:
                continue
            # Durability is PER TIER, and silence is a claim: a live-sandbox
            # hit is swept with its kernel — presenting it as a plain "local
            # file" shipped an 83.6 MB deliverable addressed by a path that
            # was dead on arrival (live 2026-07-23, F2 in misc/paths.md).
            _ephemeral = _TIER_DURABILITY.get(tier, "ephemeral") == "ephemeral"
            hits.append({"name": name, "path": str(fp), "tier": tier,
                         "locality": "local", "site": "local",
                         "durability": "ephemeral" if _ephemeral else "durable",
                         "size_bytes": int(st.st_size),
                         "mtime": _mtime_iso(st.st_mtime),
                         "opens": ("sandbox path — swept when the kernel "
                                   "stops; keep_outputs to retain (or "
                                   "register_dataset ONLY for real datasets)"
                                   if _ephemeral else "local file")})
            if len(hits) >= cap:
                return


def _live_sandbox_tier(pattern: str, hits: list, searched: dict,
                       unsearched: list, cap: int) -> None:
    """T1: every active kernel. Local jobdirs are walked; remote kernels are
    matched against their held inventory — metadata only, no transfer."""
    try:
        from core.compute.adapter import get_compute, weft_workspace
        kernels = (get_compute().sync_call("list_kernels") or {}).get("kernels", [])
    except Exception as e:  # noqa: BLE001 — substrate down ≠ files absent
        unsearched.append(f"live sandboxes unknown (substrate unreachable: "
                          f"{type(e).__name__})")
        return
    n_local = n_remote = 0
    for k in kernels:
        site = k.get("site") or "local"
        kid = k.get("kernel_id") or k.get("id")
        if site == "local":
            jd = k.get("jobdir")
            if jd:
                n_local += 1
                _walk_match(weft_workspace() / "site-local" / jd, pattern,
                            hits, "live sandbox", cap)
            continue
        # remote: consult the inventory the platform already holds
        try:
            from content.bio.tools.run_exec import _kernel_sandbox_inventory
            inv = _kernel_sandbox_inventory(kid) or {}
            n_remote += 1
        except Exception:  # noqa: BLE001
            unsearched.append(f"site '{site}' unreachable — matches there "
                              f"UNKNOWN (not absent)")
            continue
        for rel, mt in inv.items():
            base = rel.rsplit("/", 1)[-1]
            if fnmatch.fnmatchcase(base, pattern) \
                    or fnmatch.fnmatchcase(rel, pattern):
                hits.append({"name": base, "rel": rel, "tier": "live sandbox",
                             "locality": "remote", "site": site,
                             "kernel_id": kid, "size_bytes": None,
                             "mtime": _mtime_iso(mt) if mt else None,
                             "opens": f"fetches from {site} on open"})
    searched["live_kernels"] = {"local": n_local, "remote": n_remote}


def _manifest_tier(pattern: str, hits: list, searched: dict, cap: int) -> None:
    """T2: produced[] across recent execs — the name index that outlives the
    sandbox. Link-only rows (over-cap, never copied) resolve too: the name
    stays real even when the bytes never came home."""
    try:
        from core.graph import exec_records
        from core.exec.artifacts import list_artifacts
        from core.config import project_artifacts_dir
        ex_ids = exec_records.list_recent_exec_ids(_RECENT_EXECS)
    except Exception:  # noqa: BLE001
        searched["recent_execs"] = 0
        return
    searched["recent_execs"] = len(ex_ids)
    seen_paths = {h.get("path") for h in hits}
    for ex_id in ex_ids:
        for a in list_artifacts(ex_id):
            on = (a.get("original_name") or "").strip()
            base = on.rsplit("/", 1)[-1]
            if not on or not (fnmatch.fnmatchcase(base, pattern)
                              or fnmatch.fnmatchcase(on, pattern)):
                continue
            url = a.get("url") or ""
            entry = {"name": base, "rel": on, "tier": "run output",
                     "from_exec": ex_id, "size_bytes": a.get("size"),
                     "sha256": a.get("sha256")}
            if url.startswith("/artifacts/"):
                parts = url.split("/")
                fp = (Path(str(project_artifacts_dir(parts[2]))) / parts[3]
                      if len(parts) == 4 else None)
                if fp and fp.is_file():
                    if str(fp) in seen_paths:
                        continue
                    entry.update({"path": str(fp), "locality": "local",
                                  "site": "local", "opens": "served copy"})
                else:
                    entry.update({"locality": "unknown",
                                  "opens": "advertised copy missing — "
                                           "re-run to regenerate"})
            else:
                # link-only: produced but never copied (over-cap / kept remote)
                entry.update({"locality": "remote",
                              "opens": "not copied locally (over size/count "
                                       "cap) — open via its run, fetches on "
                                       "demand"})
            hits.append(entry)
            if len(hits) >= cap:
                return


def locate_project_files(pattern: str, limit: int = 50,
                         ctx: dict | None = None) -> dict:
    """Name→labeled-hits across the custody chain. Bounds are DECLARED in the
    result; ambiguity comes back as labeled candidates; unreachable tiers are
    named as unknown. `pattern` is a glob over basenames (and rel paths)."""
    limit = max(1, min(int(limit), 500))
    hits: list[dict] = []
    searched: dict = {}
    unsearched: list[str] = []

    # Tiers collect to limit+1: capping AT the limit made the total unable to
    # exceed it, so `truncated` was structurally False — silent truncation,
    # the exact class this module's honesty rules disclaim. One extra slot is
    # enough to know the cut happened; the slice below restores the limit.
    cap = limit + 1
    _live_sandbox_tier(pattern, hits, searched, unsearched, cap)
    _manifest_tier(pattern, hits, searched, cap)

    try:
        from core.config import project_data_dir, project_work_dir
        from core.projects import current_project_id
        pid = str(current_project_id() or "default")
        _walk_match(Path(str(project_data_dir(pid))), pattern, hits,
                    "user data", cap)
        _walk_match(Path(str(project_work_dir(pid))), pattern, hits,
                    "work scratch", cap)
        searched["dirs"] = ["data", "work"]
    except Exception:  # noqa: BLE001
        unsearched.append("user data/work dirs unavailable")

    truncated = len(hits) > limit
    hits = hits[:limit]
    # Scoped-first presentation: tier rank, then RECENCY. ISO-8601 strings in
    # one timezone sort lexicographically by time (a fractional second sorts
    # after its whole second — later within it), so plain string order is the
    # recency order; rows without an mtime sort last in their tier. Two-pass
    # stable sort: recency first, tier second, so tier dominates.
    _tier_rank = {"live sandbox": 0, "run output": 1,
                  "user data": 2, "work scratch": 3}
    hits.sort(key=lambda h: h.get("mtime") or "", reverse=True)
    hits.sort(key=lambda h: _tier_rank.get(h["tier"], 9))
    out = {"pattern": pattern, "matches": hits,
           "searched": {**searched,
                        "note": f"manifest tier covers the {_RECENT_EXECS} "
                                f"most recent executions"},
           "truncated": truncated}
    if unsearched:
        out["unsearched"] = unsearched
    if not hits:
        out["note"] = ("no match in any searched tier — see `searched` for "
                       "coverage bounds"
                       + ("; some tiers UNKNOWN (see `unsearched`)"
                          if unsearched else ""))
    return out
