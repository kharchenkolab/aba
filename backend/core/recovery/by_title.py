"""By-title friendly symlinks — derived view over canonical ID-named storage.

Maintains `<...>-by-title/` directories whose contents are symlinks pointing
at the canonical ID-/hash-named originals. Users browsing in Finder, `ls`,
or `cat` see meaningful slugs; ABA code paths still use canonical IDs.

See misc/mac-install.md not; see the readable-filenames discussion in
the recovery doc series. Architectural rule: these symlinks are a pure
DERIVATION of the DB. Losing them all = `aba-recover --refresh-symlinks`
rebuilds. Never the source of truth.
"""
from __future__ import annotations
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Iterable, Optional


# ─── slug ────────────────────────────────────────────────────────────────
_INVALID_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")
_RUNS_OF_HYPHEN_OR_SPACE = re.compile(r"[\s\-_]+")
_MAX_SLUG_LEN = 80


def slugify(text: str, *, fallback: str = "untitled") -> str:
    """Filesystem-safe slug from a human title.

    Rules:
      - Unicode NFKD fold (best-effort transliteration of accents/punctuation).
      - Drop anything that isn't [A-Za-z0-9._-] or space.
      - Collapse runs of spaces / hyphens / underscores into single hyphen.
      - Strip leading/trailing hyphens and dots.
      - Cap at 80 chars (long names are hostile in `ls`).
      - Empty → fallback ("untitled" by default).

    Diacritics: "Élena's plot" → "Elena-s-plot".
    CJK / unknown scripts that fold to nothing → fallback.
    Already-slugged input is idempotent: slugify(slugify(x)) == slugify(x).
    """
    if not text:
        return fallback
    # NFKD fold for transliteration, then drop combining marks
    normalized = unicodedata.normalize("NFKD", str(text))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = _INVALID_CHARS.sub(" ", ascii_only)
    collapsed = _RUNS_OF_HYPHEN_OR_SPACE.sub("-", cleaned)
    stripped = collapsed.strip("-.").strip()
    if not stripped:
        return fallback
    return stripped[:_MAX_SLUG_LEN]


def pick_slug(desired: str, *, taken: Iterable[str] = (), fallback_id: str = "",
              ext: str = "") -> str:
    """Return a slug stem for `desired` that doesn't collide with `taken`.

    `taken` is the set of *full filenames* already in the target dir
    (including any extensions). `ext` is the extension `desired` will be
    written with (so we check `<slug><ext>` against `taken`, not the bare
    stem). Returns the bare stem; caller appends `ext` to form the final
    filename.

    On collision, append `_<short-id>` (last 6 chars of fallback_id, or a
    random hex if fallback_id is empty). Deterministic when fallback_id
    is supplied — same entity always resolves to the same suffixed slug.
    """
    base = slugify(desired)
    taken_set = set(taken)
    if f"{base}{ext}" not in taken_set:
        return base
    short = (fallback_id or uuid.uuid4().hex)[-6:]
    # Strip any existing id-prefix (e.g. "prj_") to keep the suffix tight
    if "_" in short:
        short = short.split("_")[-1]
    candidate = f"{base}_{short}"
    if f"{candidate}{ext}" not in taken_set:
        return candidate
    # Last resort: append a counter (only fires when ≥2 entities share both
    # a title AND the same short-id tail — vanishingly rare)
    n = 2
    while f"{candidate}_{n}{ext}" in taken_set:
        n += 1
    return f"{candidate}_{n}"


# ─── atomic symlink ops ─────────────────────────────────────────────────
def atomic_symlink(target: Path, link_name: Path) -> None:
    """Create or replace `link_name` so it points at `target`. Atomic against
    concurrent reads — at every instant, `link_name` either is missing or
    points at the previous OR new target, never half-written.

    Implementation: symlink to a uniquely-named temp link, then `os.rename`
    onto `link_name`. POSIX rename is atomic over symlinks on macOS + Linux.

    `target` may be relative (preferred — keeps the project portable across
    different runtime roots) or absolute.
    """
    link_name = Path(link_name)
    link_name.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = link_name.with_name(f".{link_name.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        # Pure symlink — `target` text is written verbatim, never resolved.
        os.symlink(str(target), tmp_name)
        os.replace(tmp_name, link_name)
    except Exception:
        # Clean up tempfile on any failure
        try:
            tmp_name.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def clear_symlink(link_name: Path) -> bool:
    """Remove `link_name` if it exists and is a symlink. Returns True if
    something was removed.

    Skips regular files — never deletes user-dropped content that happens
    to share a name with what we'd auto-generate. (The slug picker
    collision-suffixes around this, so the symlink should have a different
    name to begin with, but be defensive.)
    """
    link_name = Path(link_name)
    if not link_name.is_symlink():
        return False
    try:
        link_name.unlink()
        return True
    except FileNotFoundError:
        return False


def existing_slugs_in(dir_path: Path) -> set[str]:
    """Names of symlinks already in `dir_path` (regular files are excluded so
    they can never collide with our slug picker's output)."""
    out: set[str] = set()
    if not dir_path.exists() or not dir_path.is_dir():
        return out
    try:
        for entry in dir_path.iterdir():
            if entry.is_symlink():
                out.add(entry.name)
    except OSError:
        pass
    return out


# ─── R2 — entity-type-aware dispatcher ──────────────────────────────────
from dataclasses import dataclass


@dataclass
class LinkSpec:
    """One by-title symlink to create or refresh.

    - category: subdir of the project (or runtime root for projects)
      e.g. "artifacts-by-title", "runs-by-title", "projects-by-title"
    - link_name: file/dir name of the symlink (slug + ext for files, slug for dirs)
    - target: relative path text written into the symlink, NOT resolved.
      Relative-from-link-parent so the project stays portable across hosts.
    """
    category: str
    link_name: str
    target: str

    # Optional: who owns this link, for collision-suffixing
    fallback_id: str = ""


# Entity types whose canonical storage is an artifact file we can link to
# by extension. Figure/table/cell all share the artifacts-by-title/ dir.
_ARTIFACT_ENTITY_TYPES = ("figure", "table", "cell")


def _basename(path_str: str) -> str:
    """Last segment of an artifact path. Tolerates either `/abs/path/x.png`
    or relative shapes — we only need the file leaf to build the symlink
    target."""
    return Path(path_str).name


def compute_entity_link(row: dict) -> Optional[LinkSpec]:
    """Map an entity row → its by-title link, if any.

    Returns None for entities that have no readable on-disk manifestation
    (results, claims, narratives, plans, workspace, etc.) or that are
    archived / deleted.

    For v1 we support artifact-typed entities only (figure/table/cell).
    Runs / threads / datasets land in R3 expansion once their work-dir
    convention is plumbed through here.
    """
    if not row:
        return None
    status = (row.get("status") or "active").lower()
    if status not in ("active",):
        return None   # archived / deleted entities don't get a link
    etype = (row.get("type") or "").lower()
    title = row.get("title") or ""

    if etype in _ARTIFACT_ENTITY_TYPES:
        artifact_path = row.get("artifact_path")
        if not artifact_path:
            return None
        ext = Path(artifact_path).suffix    # ".png", ".csv", ".md", …
        base = slugify(title)
        link_name = f"{base}{ext}" if ext else base
        target = f"../artifacts/{_basename(artifact_path)}"
        return LinkSpec(
            category="artifacts-by-title",
            link_name=link_name,
            target=target,
            fallback_id=row.get("id") or "",
        )

    return None


def compute_project_link(pid: str, title: str) -> LinkSpec:
    """The top-level projects-by-title/<slug> symlink pointing at this project's
    canonical projects/<pid>/ dir.

    Empty title → fall through to the pid itself (which is already a valid
    filename), NOT to slugify(pid) which would mangle the underscore.
    """
    name = slugify(title) if (title or "").strip() else pid
    return LinkSpec(
        category="projects-by-title",
        link_name=name,
        target=f"../projects/{pid}",
        fallback_id=pid,
    )


def title_file_contents(title: str) -> str:
    """Body of the TITLE.txt sidecar inside each project's canonical dir."""
    return (title or "").strip() + "\n"


# ─── R4 — full-rebuild refresh (DB → FS) ────────────────────────────────
def refresh_by_title_links(project_dir: Path) -> dict:
    """Rebuild every by-title symlink in a project from the live DB.

    Used after a fresh recover_project walk (the by-title dirs aren't part
    of the recovery archive, since they're derived) and as a manual repair
    via `aba-recover refresh-symlinks`. Idempotent.

    Steps:
      1. Read entities + edges from project.db.
      2. Identify superseded entities (incoming wasRevisionOf edge) — skip them.
      3. Compute the desired symlink set per category.
      4. Walk existing symlinks in each by-title dir; remove stale ones.
      5. Write missing symlinks.
      6. Refresh TITLE.txt + projects-by-title link at the runtime root.

    Returns: {"created": N, "removed": M, "unchanged": K, "categories": [...]}
    """
    import sqlite3
    project_dir = Path(project_dir).resolve()
    db_path = project_dir / "project.db"
    counts = {"created": 0, "removed": 0, "unchanged": 0, "categories": []}
    if not db_path.exists():
        return counts

    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        # Read entities
        entities = c.execute("SELECT * FROM entities").fetchall()
        # Read superseded set (dst of any wasRevisionOf edge)
        superseded = {
            r["target_id"] for r in c.execute(
                "SELECT target_id FROM entity_edges WHERE rel_type = 'wasRevisionOf'"
            ).fetchall()
        }
    finally:
        c.close()

    # Build the desired set per category
    desired: dict[str, dict[str, str]] = {}  # category → {link_name: target}
    # Sort entities deterministically so collision suffixing is stable
    for row in sorted(entities, key=lambda r: r["id"]):
        if row["id"] in superseded:
            continue
        # Normalize row to a plain dict (sqlite3.Row doesn't have .get).
        d = {k: row[k] for k in row.keys()}
        if "metadata" in d and isinstance(d["metadata"], str):
            try:
                d["metadata"] = (lambda v: __import__("json").loads(v))(d["metadata"])
            except Exception:
                pass
        spec = compute_entity_link(d)
        if spec is None:
            continue
        category_map = desired.setdefault(spec.category, {})
        # Resolve collision against everything already in this category
        ext = Path(spec.link_name).suffix
        actual_stem = pick_slug(
            Path(spec.link_name).stem,
            taken=set(category_map.keys()),
            fallback_id=spec.fallback_id,
            ext=ext,
        )
        link_name = actual_stem + ext
        category_map[link_name] = spec.target

    # Apply per category
    for category, wanted in desired.items():
        parent = project_dir / category
        existing = existing_slugs_in(parent)
        # Remove stale
        for name in existing - set(wanted.keys()):
            if clear_symlink(parent / name):
                counts["removed"] += 1
        # Write missing / verify present
        for name, target in wanted.items():
            link_path = parent / name
            if link_path.is_symlink() and os.readlink(link_path) == target:
                counts["unchanged"] += 1
                continue
            atomic_symlink(target, link_path)
            counts["created"] += 1
        counts["categories"].append(category)

    # Also remove links from categories no entity wants any more
    for category in ("artifacts-by-title",):
        if category in desired:
            continue
        parent = project_dir / category
        for name in existing_slugs_in(parent):
            if clear_symlink(parent / name):
                counts["removed"] += 1

    return counts


def refresh_project_link_at_root(project_dir: Path, registry_row: Optional[dict] = None) -> Optional[Path]:
    """Refresh the runtime-root `projects-by-title/<slug>` symlink + TITLE.txt
    for one project. Reads title from registry_row if supplied, else from
    project.json on disk.

    Returns the symlink path written, or None if nothing was written
    (e.g. project.json missing AND no registry_row supplied).
    """
    import json as _json
    project_dir = Path(project_dir).resolve()
    pid = project_dir.name
    if registry_row is None:
        pj = project_dir / "project.json"
        if not pj.exists():
            return None
        try:
            data = _json.loads(pj.read_text())
            registry_row = data.get("registry") or {}
        except Exception:
            return None
    title = (registry_row.get("name") or registry_row.get("display_name") or "").strip()
    # TITLE.txt inside the project dir
    try:
        (project_dir / "TITLE.txt").write_text(title_file_contents(title))
    except Exception:
        pass
    # projects-by-title symlink at runtime root
    runtime_root = project_dir.parent.parent  # …/projects/<pid> → …
    parent = runtime_root / "projects-by-title"
    spec = compute_project_link(pid, title)
    taken = existing_slugs_in(parent)
    # Clear any prior link that pointed at THIS pid (idempotent re-runs after
    # a rename — we don't have the cached previous name in this code path).
    for name in list(taken):
        try:
            t = os.readlink(parent / name)
            if t.endswith(f"projects/{pid}") or t == f"../projects/{pid}":
                if name != spec.link_name:
                    clear_symlink(parent / name)
                    taken.discard(name)
        except OSError:
            pass
    actual = pick_slug(spec.link_name, taken=taken, fallback_id=spec.fallback_id, ext="")
    link_path = parent / actual
    atomic_symlink(spec.target, link_path)
    return link_path
