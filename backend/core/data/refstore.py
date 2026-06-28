"""Content-addressed reference store (data.md §4.3).

The shared, deduplicated half of the data lake: genomes, transcriptomes,
indices, annotations — reference data reused across projects. Stored *by value*
(content-addressed under REFS_DIR/<sha>/), unlike the project artifact store
which stores *by reference* (a path). References are `reference` entities (hidden
from the project tree by P1's filter) carrying organism/role/assembly/source +
the content sha, discoverable via find_reference.

P4 ships the primitives; the agent orchestrates the fetch→build→register chain
(it has the conda tools env from P3). A hardcoded recursive resolver is deferred.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from core.config import REFS_DIR
from core.graph.entities import create_entity

REFERENCE = "reference"


# ── Store layout (misc/refs.md §3.1) ──────────────────────────────────────
# Three roles, so an OWNED reference (bytes in our CAS) and a LINKED one
# (bytes at a pre-existing path) are handled the same way:
#   registry/<id>.json   — the descriptor (the durable truth)
#   objects/<sha>/<name> — owned bytes pool (by-value, dedup); absent for linked
#   catalog/<path>/data  — human view → objects/<sha>/ OR an external path
def _objects_dir(root: Path) -> Path:
    return root / "objects"


def _registry_dir(root: Path) -> Path:
    return root / "registry"


def _catalog_dir(root: Path) -> Path:
    return root / "catalog"


# ── Scoped tiers (misc/refs.md §3.3) ──────────────────────────────────────
# Phase 1: refs are layered across tier roots resolved from site.yaml's `refs:`
# block (group/institution, {group}/{user}/{home}-expanded via the scope
# resolver) plus the legacy REFS_DIR as the always-present personal/default
# tier (== Phase 0 behavior when there is no site.yaml). The project tier is a
# documented follow-up.
def _tier_roots(env: Optional[dict] = None) -> list[tuple[str, Path]]:
    """[(scope, root)] in find-precedence order (narrowest → widest)."""
    env = env if env is not None else dict(os.environ)
    roots: list[tuple[str, Path]] = [("personal", Path(REFS_DIR))]
    try:
        from core.bundle.scope_resolver import (
            _read_site_yaml, _resolve_group, _expand_placeholders, _user_id)
        site_path = env.get("ABA_SITE_CONFIG")
        site = _read_site_yaml(Path(site_path)) if site_path else None
        if site:
            group = _resolve_group(env)
            user = _user_id(env)
            home = Path(env.get("HOME") or Path.home())
            refs_cfg = site.get("refs") or {}
            for scope in ("group", "institution"):
                tmpl = refs_cfg.get(scope)
                if not tmpl:
                    continue
                expanded = _expand_placeholders(str(tmpl), user=user,
                                                group=group, home=home)
                if expanded:
                    roots.append((scope, Path(expanded)))
    except Exception:  # noqa: BLE001 — tier resolution must never break the store
        pass
    return roots


def _root_for_scope(scope: str, env: Optional[dict] = None) -> Path:
    """Where register(scope=…) writes. Falls back to the personal tier when the
    requested scope isn't configured/resolvable on this box."""
    tiers = dict(_tier_roots(env))
    return tiers.get(scope) or tiers.get("personal") or Path(REFS_DIR)


def _iter_descriptors(env: Optional[dict] = None):
    """Yield every reference descriptor across the readable tier roots, in
    precedence order, first-id-wins. The discovery index — replaces
    list_entities so refs are visible across the projects/users that share a
    tier (refs.md §8). Each yielded descriptor carries `_scope`/`_root`."""
    seen: set = set()
    for scope, root in _tier_roots(env):
        rd = _registry_dir(root)
        if not rd.is_dir():
            continue
        for f in sorted(rd.glob("*.json")):
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            rid = d.get("id")
            if rid and rid not in seen:
                seen.add(rid)
                d["_scope"], d["_root"] = scope, str(root)
                yield d


def _slug(s: str) -> str:
    """Filesystem-safe catalog slug per knowhow/refs/NAMING.md."""
    s = (s or "").strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-._")
    return s or "x"


def structural_path(*, organism: Optional[str], assembly: Optional[str],
                    role: Optional[str], build: Optional[str]) -> str:
    """The human catalog path: <organism>/<assembly>/<role>/<build>, skipping
    missing facets. Empty → 'misc' (the caller appends a sha for uniqueness)."""
    parts = [_slug(p) for p in (organism, assembly, role, build) if p]
    return "/".join(parts) if parts else "misc"


def _fingerprint(p: Path) -> dict:
    """Cheap identity for a (possibly huge, linked) path — a stat, not a read."""
    st = p.stat()
    return {"path": str(p), "size": st.st_size, "mtime": int(st.st_mtime)}


def _symlink_force(link: Path, target: Path) -> None:
    """Idempotent symlink: leave it alone if already pointing at target."""
    try:
        if link.is_symlink():
            if os.readlink(link) == str(target):
                return
            link.unlink()
        elif link.exists():
            return  # a real file sits here (shouldn't happen) — don't clobber
        link.symlink_to(target)
    except FileExistsError:
        pass


def _write_descriptor(d: dict, root: Path) -> Path:
    """Write <tier>/registry/<id>.json — the one durable artifact. Bumps the
    tier's freshness marker so a per-backend index knows to rescan (refs.md §8)."""
    rd = _registry_dir(root)
    rd.mkdir(parents=True, exist_ok=True)
    fp = rd / f"{d['id']}.json"
    fp.write_text(json.dumps(d, indent=2, default=str))
    try:
        (rd / ".seq").write_text(str(int(time.time() * 1000)))
    except OSError:
        pass
    return fp


def get_reference(ref_id: str, env: Optional[dict] = None) -> Optional[dict]:
    """The descriptor for a reference id (searched across tiers), or None."""
    for d in _iter_descriptors(env):
        if d.get("id") == ref_id:
            return d
    return None


def _emit_catalog(sp: str, data_target: Path, ref_id: str, root: Path) -> Path:
    """Materialize the human catalog node under the tier root: a `data` symlink
    to the bytes and a `reference.json` back-pointer to the descriptor."""
    node = _catalog_dir(root) / sp
    node.mkdir(parents=True, exist_ok=True)
    _symlink_force(node / "data", data_target)
    _symlink_force(node / "reference.json", _registry_dir(root) / f"{ref_id}.json")
    return node


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_dir(d: Path) -> str:
    """Manifest hash of a directory: sorted (relpath, size, filesha) lines.
    Stable regardless of walk order; cheap enough for index dirs."""
    lines = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            lines.append(f"{p.relative_to(d).as_posix()}\t{p.stat().st_size}\t{_sha_file(p)}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def content_sha(path: Union[str, Path]) -> str:
    p = Path(path)
    return _sha_dir(p) if p.is_dir() else _sha_file(p)


def _find_by_sha(sha: str) -> Optional[dict]:
    for d in _iter_descriptors():
        if (d.get("identity") or {}).get("sha") == sha:
            return d
    return None


def _find_by_path(path: str) -> Optional[dict]:
    """Dedup key for LINKED refs (no content-sha): the external path itself."""
    for d in _iter_descriptors():
        if not d.get("owned", True) and d.get("artifact_path") == path:
            return d
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_reference(
    src_path: Union[str, Path],
    *,
    organism: Optional[str] = None,
    role: Optional[str] = None,
    source: Optional[str] = None,
    assembly: Optional[str] = None,
    derived_from: Optional[Union[str, list[str]]] = None,
    scope: str = "institution",
    title: Optional[str] = None,
    version: Optional[str] = None,
    mode: str = "copy",
    acquisition: Optional[dict] = None,
) -> str:
    """Register a reference + write its descriptor + human `catalog/` entry
    (misc/refs.md §3). Returns the reference id — the existing one on a dedup
    hit (no re-copy / re-link).

    `mode="copy"` (default) → **owned**: content-hash the bytes and copy them
    into `objects/<sha>/`. `mode="link"` → **linked**: adopt a pre-existing path
    (a cluster genome store, iGenomes, …) in place, *no copy*; identity is a
    cheap fingerprint (path+size+mtime), the full sha computed lazily later.

    Lineage (`derived_from`) is recorded in the **descriptor**, not an entity
    edge: the `reference` type forbids out-edges, so the pre-2026-06-29
    wasDerivedFrom-edge path errored at validation."""
    src = Path(src_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"reference source not found: {src}")
    # Refuse pathological sources (a copy is O(bytes); even a link of the whole
    # home/root as one "reference" is nonsense). 2026-06-04 a test pointed copy
    # at /tmp and burnt 304 GB before being caught — fail loudly.
    forbidden = {Path("/tmp").resolve(), Path("/var/tmp").resolve(),
                 Path("/").resolve(), Path.home().resolve()}
    if src in forbidden:
        raise ValueError(
            f"register_reference refuses pathological source {src!r}. "
            f"Reference data should be a specific dataset/genome/index dir, "
            f"not a system temp / home / root directory."
        )
    if mode not in ("copy", "link"):
        raise ValueError(f"register_reference: mode must be 'copy' or 'link', got {mode!r}")
    owned = (mode == "copy")
    root = _root_for_scope(scope)  # which tier this reference is written to

    if owned:
        sha = content_sha(src)
        existing = _find_by_sha(sha)
        if existing is not None:
            return existing["id"]
        cas = _objects_dir(root) / sha
        artifact = cas / src.name
        if not cas.exists():
            cas.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, artifact)
            else:
                shutil.copy2(src, artifact)
        elif not artifact.exists():
            children = list(cas.iterdir())
            artifact = children[0] if children else cas
        identity = {"kind": "content-sha", "sha": sha,
                    "fingerprint": None, "verified_at": _now_iso()}
        build = version or f"sha_{sha[:8]}"
        meta_sha: Optional[str] = sha
    else:  # linked — adopt in place, no copy
        existing = _find_by_path(str(src))
        if existing is not None:
            return existing["id"]
        artifact = src
        identity = {"kind": "fingerprint", "sha": None,
                    "fingerprint": _fingerprint(src), "verified_at": _now_iso()}
        build = version or f"link_{hashlib.sha256(str(src).encode()).hexdigest()[:8]}"
        meta_sha = None

    _ref_targets = [derived_from] if isinstance(derived_from, str) else (derived_from or [])
    sp = structural_path(organism=organism, assembly=assembly, role=role, build=build)
    if sp == "misc":
        sp = f"misc/{build}"
    derived_title = title or f"{organism or 'ref'}:{role or src.name}"

    from core.graph.derivation import imported, SYSTEM_ACTOR
    meta = {"scope": scope, "sha": meta_sha, "organism": organism, "role": role,
            "assembly": assembly, "source": source, "structural_path": sp,
            "owned": owned}
    eid = create_entity(
        entity_type=REFERENCE,
        title=derived_title,
        artifact_path=str(artifact),
        derivation=imported(source or "reference"),  # always imported; lineage → descriptor
        actor=SYSTEM_ACTOR,
        metadata=meta,
    )

    descriptor = {
        "id": eid,
        "owned": owned,
        "identity": identity,
        "organism": organism, "assembly": assembly, "role": role,
        "structural_path": sp, "title": derived_title,
        "acquisition": acquisition or {"mode": ("linked" if not owned else "imported"),
                                       "source": source},
        "derivation": ({"kind": "derived_from", "sources": list(_ref_targets)}
                       if _ref_targets else {"kind": "imported", "source": source}),
        "scope": scope, "artifact_path": str(artifact),
        "registered_at": _now_iso(),
    }
    _write_descriptor(descriptor, root)
    _emit_catalog(sp, artifact, eid, root)
    return eid


def _row(d: dict) -> dict:
    ident = d.get("identity") or {}
    return {"id": d.get("id"), "title": d.get("title"),
            "artifact_path": d.get("artifact_path"),
            "organism": d.get("organism"), "role": d.get("role"),
            "assembly": d.get("assembly"),
            "source": (d.get("acquisition") or {}).get("source"),
            "sha": ident.get("sha"), "scope": d.get("_scope")}


def find_reference(organism: Optional[str] = None, role: Optional[str] = None,
                   assembly: Optional[str] = None) -> Optional[dict]:
    """First reference matching the given facets, or None."""
    for r in list_references(organism=organism, role=role, assembly=assembly):
        return r
    return None


def promote_reference(ref_id: str, to_scope: str,
                      env: Optional[dict] = None) -> dict:
    """Move a reference UP a tier (project → group → institution): copy owned
    bytes into the destination tier's objects pool (idempotent), write the
    descriptor + catalog there with the new scope, and remove it from the source
    tier. Leaves source objects for GC (refs.md §12). Permission-gated in
    practice by write access to the destination tier (institution = curator)."""
    d = get_reference(ref_id, env)
    if not d:
        raise ValueError(f"unknown reference {ref_id}")
    src_root = Path(d.get("_root") or REFS_DIR)
    src_scope = d.get("_scope")
    if src_scope == to_scope:
        return {"reference_id": ref_id, "scope": to_scope, "moved": False,
                "note": "already at this scope"}
    dst_root = _root_for_scope(to_scope, env)
    if dst_root.resolve() == src_root.resolve():
        return {"reference_id": ref_id, "scope": to_scope, "moved": False,
                "note": f"tier {to_scope!r} resolves to the same path as the source"}

    new_d = {k: v for k, v in d.items() if not k.startswith("_")}
    new_d["scope"] = to_scope
    if d.get("owned", True):
        sha = (d.get("identity") or {}).get("sha")
        if sha:
            src_obj = _objects_dir(src_root) / sha
            dst_obj = _objects_dir(dst_root) / sha
            if src_obj.exists() and not dst_obj.exists():
                shutil.copytree(src_obj, dst_obj)
            name = Path(d.get("artifact_path") or "").name
            if name and (dst_obj / name).exists():
                new_d["artifact_path"] = str(dst_obj / name)

    _write_descriptor(new_d, dst_root)
    _emit_catalog(new_d.get("structural_path") or "misc",
                  Path(new_d["artifact_path"]), ref_id, dst_root)
    # Remove from the source tier (descriptor + catalog symlinks); objects stay
    # for GC since other refs may share the sha.
    try:
        (_registry_dir(src_root) / f"{ref_id}.json").unlink()
    except OSError:
        pass
    src_cat = _catalog_dir(src_root) / (d.get("structural_path") or "")
    for n in ("data", "reference.json"):
        p = src_cat / n
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass
    return {"reference_id": ref_id, "from": src_scope, "to": to_scope,
            "moved": True, "artifact_path": new_d.get("artifact_path")}


def list_references(organism: Optional[str] = None, role: Optional[str] = None,
                    assembly: Optional[str] = None) -> list[dict]:
    out = []
    for d in _iter_descriptors():
        if organism and d.get("organism") != organism:
            continue
        if role and d.get("role") != role:
            continue
        if assembly and d.get("assembly") != assembly:
            continue
        out.append(_row(d))
    return out
