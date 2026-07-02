"""Versioned-release substrate for the slim-SIF deployment (misc/slim_sif_deploy.md §3–§4).

Slim SIF puts ABA's code + envs + tools on MUTABLE shared FS with multiple live consumers (OOD
sessions holding the mount, in-flight Slurm jobs, new jobs). To buy back the consistency a fat
image gets for free, releases are IMMUTABLE trees under `$ABA_SHARE/releases/<ver>` and `current`
is an atomic symlink to the active one. Two invariants make it safe:

  * **Atomic swap, never in-place mutation** — an upgrade BUILDS a new release and repoints
    `current` via an atomic rename; a running job's tree is never mutated under it.
  * **Pin-on-launch** — a session/job resolves `current` → a concrete release ONCE at start
    (`ABA_RELEASE_ID`), and reuses THAT for its whole life + every job (and Nextflow auto-resume)
    it spawns. Re-reading live `current` per job is the source of session-vs-job skew and the
    `-resume` cache-invalidation hazard (§2).

Everything here is a NO-OP when `$ABA_SHARE` is unset — personal installs and the fat SIF (which
carry a self-consistent image) never set it, so they are entirely unaffected.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional


def _version_key(ver: str):
    """Numeric-aware ordering so releases sort by version, not lexically (2024.11 > 2024.9,
    v10 > v2). Splits into digit / non-digit runs; a mixed key stays stable for date, semver,
    and sha-suffixed names."""
    return [(1, int(p)) if p.isdigit() else (0, p) for p in re.split(r"(\d+)", ver) if p]


def share_root(share: Optional[str] = None) -> Optional[Path]:
    s = share or os.environ.get("ABA_SHARE")
    return Path(s) if s else None


def _releases(share: Path) -> Path:
    return share / "releases"


def _current(share: Path) -> Path:
    return share / "current"


def _prev_file(share: Path) -> Path:
    return share / ".previous"


def resolve_current(share: Optional[str] = None) -> Optional[str]:
    """The release id `current` points at (its target's basename), or None. Live read — callers
    that need pinning use active_release_id(), not this."""
    root = share_root(share)
    if not root:
        return None
    cur = _current(root)
    try:
        if cur.is_symlink():                 # `current` is ALWAYS a symlink (promote makes it one);
            return os.path.basename(os.path.realpath(cur))   # a real dir named `current` isn't valid
    except OSError:
        return None
    return None


def release_path(release_id: str, share: Optional[str] = None) -> Optional[Path]:
    """Absolute path of a release tree, or None if it doesn't exist."""
    root = share_root(share)
    if not root or not release_id:
        return None
    p = _releases(root) / release_id
    return p if p.exists() else None


def list_releases(share: Optional[str] = None) -> list[str]:
    root = share_root(share)
    if not root or not _releases(root).is_dir():
        return []
    return sorted((p.name for p in _releases(root).iterdir() if p.is_dir()), key=_version_key)


def read_manifest(ver: str, share: Optional[str] = None) -> dict:
    """A release's provenance (version, git sha, env lockfiles, nextflow/jdk versions, build time)
    — written by `aba release build` (aba-vbc build.sh), read here for `list`/audit. {} if absent."""
    p = release_path(ver, share)
    if not p:
        return {}
    mf = p / "manifest.json"
    try:
        return json.loads(mf.read_text()) if mf.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def compute_referenced(share: Optional[str] = None) -> set:
    """Release ids that LIVE state pins — the current release + the `release_id` of every
    running/queued job across all project DBs (e.g. a long Nextflow head pinned to an older
    release). This is the refcount that makes GC safe against in-flight work: GC must never delete
    a release something is still using. Best-effort; a scan error just yields fewer refs (GC then
    protects less, so we ALSO always protect current + the newest `keep`)."""
    refs: set = set()
    cur = resolve_current(share)
    if cur:
        refs.add(cur)
    try:
        from core.config import PROJECTS_DIR
        import sqlite3
        if not PROJECTS_DIR.exists():
            return refs
        for proj in PROJECTS_DIR.iterdir():
            db = proj / "project.db"
            if not proj.is_dir() or not db.exists():
                continue
            try:
                c = sqlite3.connect(db)
                for (params,) in c.execute(
                        "SELECT params FROM jobs WHERE status IN ('running','queued')"):
                    try:
                        rid = (json.loads(params or "{}") or {}).get("release_id")
                        if rid:
                            refs.add(rid)
                    except Exception:  # noqa: BLE001
                        pass
                c.close()
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return refs


def verify(ver: str, share: Optional[str] = None) -> dict:
    """Structural pre-promote gate: a release tree must exist, carry a repo + a manifest with a
    version, before it can become `current`. Returns {ok, checks, missing}. The LIVE smoke (health
    200, a tiny run_python, a `-profile test` nextflow) runs on the deploy host against
    release_path(ver) — this is the on-disk half that's checkable anywhere."""
    p = release_path(ver, share)
    checks, missing = {}, []
    checks["tree_exists"] = bool(p)
    if not p:
        return {"ok": False, "checks": checks, "missing": ["release tree"]}
    checks["has_repo"] = (p / "repo").exists()
    mf = read_manifest(ver, share)
    checks["has_manifest"] = bool(mf)
    checks["manifest_version_matches"] = (mf.get("version") == ver) if mf else False
    for k, v in checks.items():
        if not v:
            missing.append(k)
    return {"ok": not missing, "checks": checks, "missing": missing}


def active_release_id() -> Optional[str]:
    """The release THIS process is pinned to — the pin-on-launch value. `ABA_RELEASE_ID` (stamped
    once by the OOD launcher / a job's env) wins; else a live read of `current`; else None (no
    release layout → personal/fat, nothing to pin)."""
    return os.environ.get("ABA_RELEASE_ID") or resolve_current()


def stamp_release(params: dict) -> dict:
    """Pin-on-launch for a background job: record the release it was submitted under, so it (and
    its Nextflow auto-resume) reuse THAT release even if `current` moves. No-op without a release
    layout (returns params unchanged) → personal/fat installs carry no release_id."""
    rid = active_release_id()
    if rid and not params.get("release_id"):
        return {**params, "release_id": rid}
    return params


def _atomic_point(link: Path, target: Path) -> None:
    """Point `link` at `target` atomically (create a temp symlink, then rename over — rename is
    atomic on POSIX, so no consumer ever sees a missing/half link)."""
    tmp = link.parent / f".{link.name}.tmp.{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, link)   # atomic; replaces an existing symlink


def promote(ver: str, share: Optional[str] = None) -> dict:
    """Repoint `current -> releases/<ver>` atomically, recording the prior release for rollback.
    Never mutates a release tree. Raises if the release doesn't exist (promote only a built tree)."""
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE — release management is slim-SIF only")
    rel = _releases(root) / ver
    if not rel.is_dir():
        raise FileNotFoundError(f"release {ver!r} not built at {rel}")
    prev = resolve_current(str(root))
    if prev and prev != ver:
        _prev_file(root).write_text(prev)
    _atomic_point(_current(root), rel)
    return {"current": ver, "previous": prev}


def rollback(share: Optional[str] = None) -> dict:
    """Repoint `current` back to the previously-promoted release (instant recovery; the old tree
    is still on disk)."""
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE")
    pf = _prev_file(root)
    if not pf.exists():
        raise FileNotFoundError("no previous release recorded")
    prev = pf.read_text().strip()
    if not (_releases(root) / prev).is_dir():
        raise FileNotFoundError(f"previous release {prev!r} is gone")
    return promote(prev, str(root))


def gc(share: Optional[str] = None, *, keep: int = 2,
       referenced: "set[str] | tuple[str, ...] | None" = None) -> dict:
    """Delete releases that are neither `current`, the recorded previous, referenced by LIVE state
    (running/queued jobs — computed automatically when `referenced` is None), nor within the newest
    `keep`. Never deletes a pinned/current release. Pass an explicit `referenced` to override the
    live scan (used by tests)."""
    import shutil
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE")
    if referenced is None:
        referenced = compute_referenced(str(root))           # the live refcount
    cur = resolve_current(str(root))
    prev = _prev_file(root).read_text().strip() if _prev_file(root).exists() else None
    protect = (set(referenced) | {cur, prev}) - {None}
    all_rel = list_releases(str(root))                        # version-ordered
    if keep > 0:
        protect |= set(all_rel[-keep:])                       # keep the newest N by version
    removed = []
    for ver in all_rel:
        if ver in protect:
            continue
        shutil.rmtree(_releases(root) / ver, ignore_errors=True)
        removed.append(ver)
    # After removing orphaned RELEASES, sweep the components none of the survivors reference.
    comp = gc_components(str(root))
    return {"removed": removed, "kept": [v for v in all_rel if v not in removed],
            "components_removed": comp["removed"]}


def build_mock(ver: str, src: str, share: Optional[str] = None) -> Path:
    """TEST/mock only: materialize `releases/<ver>` by SYMLINKING an existing install tree as the
    release payload (a real `aba release build` compiles envs — deploy-side, heavy). Lets the
    promote/rollback/pin lifecycle be exercised without a multi-GB env build."""
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE")
    rel = _releases(root) / ver
    rel.mkdir(parents=True, exist_ok=True)
    link = rel / "repo"
    if not link.exists():
        link.symlink_to(src)
    (rel / "manifest.json").write_text(f'{{"version": "{ver}", "repo": "{src}"}}')
    return rel


# ── content-addressed components (a release is a COMPOSITION, not a copy — misc/slim_sif_deploy.md §1)
# A release re-links shared, immutable components keyed by content: repo/<git-sha>, env/<lockfile-hash>,
# opt/<tools-hash>. So a CODE-ONLY upgrade re-links the SAME (multi-GB) env component — zero copy —
# and the env is rebuilt only when its lockfile actually changes.

def _components(share: Path) -> Path:
    return share / "components"


def component_path(kind: str, cid: str, share: Optional[str] = None) -> Optional[Path]:
    root = share_root(share)
    if not root or not cid:
        return None
    p = _components(root) / kind / cid
    return p if p.exists() else None


def hash_files(paths: "list[str]") -> str:
    """Content id for an env/opt component: sha1 over the concatenated lockfile bytes (order-stable).
    Unchanged lockfiles → same id → dedup (build reuses the existing component)."""
    h = hashlib.sha1()
    for p in paths:
        try:
            h.update(Path(p).read_bytes())
        except OSError:
            h.update(b"\0missing\0")
        h.update(b"\0")
    return h.hexdigest()[:16]


def ensure_component(kind: str, cid: str, builder, share: Optional[str] = None) -> Path:
    """Content-addressed build-or-reuse. If `components/<kind>/<cid>` exists → return it (DEDUP: the
    expensive env compile is skipped when the lockfile hash is unchanged). Else populate a temp dir
    via builder(tmp) and atomically publish it (rename), so a component is never half-built."""
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE")
    dest = _components(root) / kind / cid
    if dest.exists():
        return dest                                        # reuse — no rebuild, no copy
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{cid}.building.{os.getpid()}"
    import shutil
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    builder(tmp)
    os.replace(tmp, dest)                                  # atomic publish
    return dest


def compose_release(ver: str, *, repo: str, env: str, opt: str,
                    share: Optional[str] = None, built_at: Optional[str] = None) -> Path:
    """A release = relative symlinks to component versions + a manifest recording which it pins.
    ~0 bytes; the env is shared by reference across every release pinning the same env id."""
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE")
    rel = _releases(root) / ver
    rel.mkdir(parents=True, exist_ok=True)
    comps = {"repo": repo, "env": env, "opt": opt}
    for kind, cid in comps.items():
        cp = _components(root) / kind / cid
        link = rel / kind
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(os.path.relpath(cp, rel))          # relative → portable if $ABA_SHARE moves
    manifest = {"version": ver, "components": comps}
    if built_at:
        manifest["built_at"] = built_at
    (rel / "manifest.json").write_text(json.dumps(manifest))
    return rel


def release_components(ver: str, share: Optional[str] = None) -> dict:
    return (read_manifest(ver, share).get("components") or {})


def component_referenced(share: Optional[str] = None) -> dict:
    """{kind: set(cids)} that ANY release pins. Components not here are orphans GC can remove."""
    refs: dict = {}
    for ver in list_releases(share):
        for kind, cid in release_components(ver, share).items():
            refs.setdefault(kind, set()).add(cid)
    return refs


def gc_components(share: Optional[str] = None) -> dict:
    """Delete component dirs no surviving release references. Runs AFTER release GC (so removing a
    release first frees its components). A referenced (still-pinned) component is never touched."""
    import shutil
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE")
    refs = component_referenced(str(root))
    cdir = _components(root)
    removed: list = []
    if not cdir.is_dir():
        return {"removed": removed}
    for kind_dir in cdir.iterdir():
        if not kind_dir.is_dir():
            continue
        keep = refs.get(kind_dir.name, set())
        for comp in kind_dir.iterdir():
            if comp.name in keep or comp.name.startswith("."):
                continue
            shutil.rmtree(comp, ignore_errors=True)
            removed.append(f"{kind_dir.name}/{comp.name}")
    return {"removed": removed}


def compute_version(repo_dir: str, *, now=None) -> str:
    """Version id for a build: the git TAG at HEAD if the checkout sits on one (release tags win —
    per the deploy decision), else `YYYY.MM.DD-<short-sha>` (chronological + code-identifying, no tag
    needed). Uses `cwd=` not `git -C` (RHEL7 build hosts ship git 1.8.3, which lacks `-C`)."""
    import subprocess

    def _git(*a):
        try:
            return subprocess.run(["git", *a], cwd=str(repo_dir),
                                  capture_output=True, text=True, timeout=15)
        except Exception:  # noqa: BLE001
            return None
    tag = _git("describe", "--exact-match", "--tags", "HEAD")
    if tag and tag.returncode == 0 and tag.stdout.strip():
        return tag.stdout.strip()                          # a release tag → use it verbatim
    sha_r = _git("rev-parse", "--short", "HEAD")
    sha = (sha_r.stdout.strip() if sha_r and sha_r.returncode == 0 else "") or "nosha"
    if now is None:
        from datetime import date
        now = date.today()
    return f"{now.strftime('%Y.%m.%d')}-{sha}"


def _cli(argv: "list[str] | None" = None) -> int:
    """`python -m core.release <cmd>` — the admin surface (aba-vbc's build.sh/deploy.sh call this)."""
    import argparse, json
    ap = argparse.ArgumentParser(prog="aba-release")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("resolve")
    p = sub.add_parser("promote"); p.add_argument("ver")
    v = sub.add_parser("verify"); v.add_argument("ver")
    sub.add_parser("rollback")
    g = sub.add_parser("gc"); g.add_argument("--keep", type=int, default=2)
    b = sub.add_parser("build-mock"); b.add_argument("ver"); b.add_argument("src")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        cur = resolve_current()
        print(json.dumps({
            "current": cur, "referenced_by_live": sorted(compute_referenced()),
            "releases": [{"version": r, "current": r == cur,
                          "manifest": read_manifest(r) or None} for r in list_releases()],
        }, indent=2))
    elif a.cmd == "resolve":
        print(json.dumps({"current": resolve_current(), "active": active_release_id()}))
    elif a.cmd == "promote":
        print(json.dumps(promote(a.ver)))
    elif a.cmd == "verify":
        r = verify(a.ver)
        print(json.dumps(r))
        return 0 if r["ok"] else 1
    elif a.cmd == "rollback":
        print(json.dumps(rollback()))
    elif a.cmd == "gc":
        print(json.dumps(gc(keep=a.keep)))
    elif a.cmd == "build-mock":
        print(str(build_mock(a.ver, a.src)))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
