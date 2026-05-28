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
import shutil
from pathlib import Path
from typing import Optional, Union

from core.config import REFS_DIR
from core.graph.entities import create_entity, get_entity, list_entities
from core.graph.edges import add_edge

REFERENCE = "reference"


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
    for e in list_entities(type_filter=REFERENCE):
        if (e.get("metadata") or {}).get("sha") == sha:
            return e
    return None


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
) -> str:
    """Place a reference into the content-addressed store (dedup by content) and
    register it as a `reference` entity. Returns the entity id — the existing one
    if identical content is already stored (no re-copy)."""
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"reference source not found: {src}")
    sha = content_sha(src)

    existing = _find_by_sha(sha)
    if existing is not None:
        return existing["id"]

    cas = REFS_DIR / sha
    if not cas.exists():
        cas.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, cas / src.name)
            artifact = cas / src.name
        else:
            shutil.copy2(src, cas / src.name)
            artifact = cas / src.name
    else:
        # CAS dir exists (shouldn't happen without an entity, but tolerate):
        # point at whatever single child is there.
        children = list(cas.iterdir())
        artifact = children[0] if children else cas

    meta = {"scope": scope, "sha": sha, "organism": organism,
            "role": role, "assembly": assembly, "source": source}
    eid = create_entity(
        entity_type=REFERENCE,
        title=title or f"{organism or 'ref'}:{role or src.name}",
        artifact_path=str(artifact),
        metadata=meta,
    )
    for target in ([derived_from] if isinstance(derived_from, str) else (derived_from or [])):
        add_edge(eid, target, "wasDerivedFrom")
    return eid


def _row(e: dict) -> dict:
    meta = e.get("metadata") or {}
    return {"id": e["id"], "title": e["title"], "artifact_path": e["artifact_path"],
            "organism": meta.get("organism"), "role": meta.get("role"),
            "assembly": meta.get("assembly"), "source": meta.get("source"),
            "sha": meta.get("sha")}


def find_reference(organism: Optional[str] = None, role: Optional[str] = None,
                   assembly: Optional[str] = None) -> Optional[dict]:
    """First reference matching the given facets, or None."""
    for r in list_references(organism=organism, role=role, assembly=assembly):
        return r
    return None


def list_references(organism: Optional[str] = None, role: Optional[str] = None,
                    assembly: Optional[str] = None) -> list[dict]:
    out = []
    for e in list_entities(type_filter=REFERENCE):
        meta = e.get("metadata") or {}
        if organism and meta.get("organism") != organism:
            continue
        if role and meta.get("role") != role:
            continue
        if assembly and meta.get("assembly") != assembly:
            continue
        out.append(_row(e))
    return out
