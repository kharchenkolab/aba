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

import os
from pathlib import Path
from typing import Optional


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
        if cur.is_symlink() or cur.exists():
            return os.path.basename(os.path.realpath(cur))
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
    return sorted(p.name for p in _releases(root).iterdir() if p.is_dir())


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
       referenced: "set[str] | tuple[str, ...]" = ()) -> dict:
    """Delete releases that are neither `current`, the recorded previous, in `referenced` (pinned
    by a live session/job), nor within the newest `keep`. Never deletes a pinned/current release."""
    import shutil
    root = share_root(share)
    if not root:
        raise RuntimeError("no $ABA_SHARE")
    cur = resolve_current(str(root))
    prev = _prev_file(root).read_text().strip() if _prev_file(root).exists() else None
    protect = set(referenced) | {cur, prev} - {None}
    all_rel = list_releases(str(root))
    protect |= set(all_rel[-keep:]) if keep > 0 else set()   # keep the newest N
    removed = []
    for ver in all_rel:
        if ver in protect:
            continue
        shutil.rmtree(_releases(root) / ver, ignore_errors=True)
        removed.append(ver)
    return {"removed": removed, "kept": [v for v in all_rel if v not in removed]}


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


def _cli(argv: "list[str] | None" = None) -> int:
    """`python -m core.release <cmd>` — the admin surface (aba-vbc's build.sh/deploy.sh call this)."""
    import argparse, json
    ap = argparse.ArgumentParser(prog="aba-release")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("resolve")
    p = sub.add_parser("promote"); p.add_argument("ver")
    sub.add_parser("rollback")
    g = sub.add_parser("gc"); g.add_argument("--keep", type=int, default=2)
    b = sub.add_parser("build-mock"); b.add_argument("ver"); b.add_argument("src")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print(json.dumps({"releases": list_releases(), "current": resolve_current()}, indent=2))
    elif a.cmd == "resolve":
        print(json.dumps({"current": resolve_current(), "active": active_release_id()}))
    elif a.cmd == "promote":
        print(json.dumps(promote(a.ver)))
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
