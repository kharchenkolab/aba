"""Recovery walker: rebuild a project DB from on-disk sidecars + jsonl logs.

The inverse of the scribe. Reads `project.json`, `entities/*.json`,
`edges*.jsonl`, `threads/*.jsonl`, and `.exec/*.json` files under a
projects/<pid>/ directory and reconstructs an equivalent SQLite project.db.

Backfill mode goes the other way (live DB → FS sidecars) and is used to
repair drift (misc/recovery.md § 10.1).
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


# ─── result types ───────────────────────────────────────────────────────────
@dataclass
class RecoverReport:
    pid: str
    source_dir: str
    target_db: str
    entities: int = 0
    edges_applied: int = 0
    edges_removed: int = 0
    edge_snapshots_read: int = 0
    edge_lines_skipped: int = 0
    messages: int = 0
    message_clears: int = 0
    execs: int = 0
    artifacts_seen: int = 0
    renamed_from_pid: Optional[str] = None  # I2 — collision auto-rename
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "source_dir": self.source_dir,
            "target_db": self.target_db,
            "entities": self.entities,
            "edges_applied": self.edges_applied,
            "edges_removed": self.edges_removed,
            "edge_snapshots_read": self.edge_snapshots_read,
            "edge_lines_skipped": self.edge_lines_skipped,
            "messages": self.messages,
            "message_clears": self.message_clears,
            "execs": self.execs,
            "artifacts_seen": self.artifacts_seen,
            "renamed_from_pid": self.renamed_from_pid,
            "warnings": list(self.warnings),
        }


# ─── helpers ────────────────────────────────────────────────────────────────
def _iter_jsonl(p: Path):
    """Yield parsed JSON objects from a .jsonl file. A torn (unterminated)
    last line is silently dropped — see misc/recovery.md § 7."""
    if not p.exists():
        return
    with p.open() as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except json.JSONDecodeError:
                # Likely a torn write at crash time. Skip the offender; earlier
                # lines are still good.
                continue


def _read_json_safe(p: Path) -> Optional[dict]:
    """Read + parse a JSON file. Returns None on missing or unparseable
    (torn write) — recovery silently skips."""
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ─── project-ID collision handling (I2) ─────────────────────────────────────
def _existing_pids() -> set[str]:
    """Pids already registered in the workspace's project registry."""
    try:
        from core import projects as _projects   # noqa: PLC0415
        return {p["id"] for p in _projects._load()}
    except Exception:
        return set()


def _gen_fresh_pid() -> str:
    return "prj_" + uuid.uuid4().hex[:8]


# ─── path normalization (I1) ────────────────────────────────────────────────
def _normalize_path(s: Optional[str], src_proot: Optional[str], tgt_proot: Optional[str]) -> Optional[str]:
    """Rewrite an absolute path whose prefix matches the source project dir
    so it instead points under the target project dir. Returns the input
    unchanged when nothing matches — keeps non-path strings (UUIDs, IDs,
    free text) safe."""
    if not isinstance(s, str) or not src_proot or not tgt_proot:
        return s
    if src_proot == tgt_proot:
        return s
    if s.startswith(src_proot):
        return tgt_proot + s[len(src_proot):]
    return s


# ─── entity / edge / message / exec writers (direct SQL, no scribe) ─────────
def _insert_entity(c: sqlite3.Connection, payload: dict,
                   src_proot: Optional[str] = None,
                   tgt_proot: Optional[str] = None) -> None:
    """Insert one entity row from a sidecar payload. INSERT OR REPLACE since
    recovery is idempotent (drift detector + repeated runs).

    If src_proot/tgt_proot differ, rewrites artifact_path (and string values
    inside producing_params/metadata that match the source-project prefix)."""
    metadata = payload.get("metadata")
    if isinstance(metadata, (dict, list)):
        metadata = json.dumps(metadata)
    producing_params = payload.get("producing_params")
    if isinstance(producing_params, (dict, list)):
        producing_params = json.dumps(producing_params)
    tags = payload.get("tags")
    if isinstance(tags, (list, dict)):
        tags = json.dumps(tags)
    artifact_path = _normalize_path(payload.get("artifact_path"), src_proot, tgt_proot)
    sql = (
        "INSERT OR REPLACE INTO entities "
        "(id, type, title, status, artifact_path, producing_params, "
        " parent_entity_id, scenario_of, metadata, tags, notes, pinned, "
        " display_path, exec_id, artifact_kind, artifact_idx, "
        " deleted_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    c.execute(sql, (
        payload.get("id"),
        payload.get("type"),
        payload.get("title") or "(unrecovered)",
        payload.get("status") or "active",
        artifact_path,
        producing_params,
        payload.get("parent_entity_id"),
        payload.get("scenario_of"),
        metadata,
        tags,
        payload.get("notes"),
        1 if payload.get("pinned") else 0,
        payload.get("display_path"),
        payload.get("exec_id"),
        payload.get("artifact_kind"),
        payload.get("artifact_idx"),
        payload.get("deleted_at"),
        payload.get("created_at") or payload.get("_ts"),
        payload.get("updated_at") or payload.get("_ts"),
    ))


def _insert_exec(c: sqlite3.Connection, payload: dict, sidecar_path: Path,
                 src_proot: Optional[str] = None,
                 tgt_proot: Optional[str] = None) -> None:
    """Insert one execution_records row from a sidecar payload.

    The exec sidecar's record_path is normalized to the target project's path
    (so the live DB doesn't hold dead pointers after a cross-host import)."""
    record_path = _normalize_path(str(sidecar_path), src_proot, tgt_proot)
    c.execute(
        "INSERT OR REPLACE INTO execution_records "
        "(exec_id, thread_id, run_id, tool_use_id, tool_name, status, "
        " code_hash, record_path, started_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            payload.get("exec_id"),
            payload.get("thread_id") or "",
            payload.get("run_id"),
            payload.get("tool_use_id"),
            payload.get("tool_name") or "unknown",
            payload.get("status") or "ok",
            payload.get("code_hash"),
            record_path,
            payload.get("started_at") or "",
            payload.get("completed_at"),
        ),
    )


def _apply_edge_add(c: sqlite3.Connection, row: dict) -> None:
    meta = row.get("meta")
    if isinstance(meta, (dict, list)):
        meta = json.dumps(meta)
    c.execute(
        "INSERT OR IGNORE INTO entity_edges "
        "(source_id, target_id, rel_type, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (row["src"], row["dst"], row["rel"], meta, row.get("ts") or ""),
    )


def _apply_edge_remove(c: sqlite3.Connection, row: dict) -> None:
    c.execute(
        "DELETE FROM entity_edges WHERE source_id = ? AND target_id = ? AND rel_type = ?",
        (row["src"], row["dst"], row["rel"]),
    )


def _insert_message(c: sqlite3.Connection, row: dict) -> None:
    content = row.get("content")
    if isinstance(content, (list, dict)):
        content = json.dumps(content)
    sql = (
        "INSERT INTO messages (id, entity_id, focus_entity_id, thread_id, role, content, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    c.execute(sql, (
        row.get("id"),
        row.get("entity_id") or "workspace",
        row.get("focus_entity_id"),
        row.get("thread_id"),
        row.get("role") or "user",
        content or "",
        row.get("ts") or "",
    ))


# ─── main walker ────────────────────────────────────────────────────────────
def recover_project(
    source_dir: Path,
    *,
    target_db: Optional[Path] = None,
    target_pid: Optional[str] = None,
    dry_run: bool = False,
) -> RecoverReport:
    """Rebuild a project DB from the on-disk recovery archive.

    Args:
        source_dir: path to projects/<pid>/.
        target_db: where to write the rebuilt SQLite. If None and not dry_run,
                   writes to <source_dir>/project.db (in-place recovery).
                   For dry_run=True, a tempfile is used and reported back.
        target_pid: not used for same-host recovery; reserved for I2.
        dry_run: write to a tempfile DB (caller can compare against live DB).

    Returns:
        RecoverReport with counts + warnings.
    """
    source_dir = Path(source_dir).resolve()
    project_json_path = source_dir / "project.json"
    project_meta = _read_json_safe(project_json_path) or {}
    candidate_pid = target_pid or project_meta.get("pid") or source_dir.name
    pid = candidate_pid
    report = RecoverReport(pid=pid, source_dir=str(source_dir), target_db="")

    # I2 — project-ID collision. If the candidate pid already exists in the
    # target host's registry AND the source dir isn't this project's own
    # current home, generate a fresh one. Skipping the "own home" case is
    # essential — recovering a project from its own runtime/projects/<pid>/
    # is the standard same-host disaster recovery, NOT a collision. Skip
    # entirely in dry-run so the drift detector doesn't perturb live state.
    if not dry_run and target_pid is None:
        existing = _existing_pids()
        # Compute the current project's home for this pid; if our source dir
        # already lives there, this is self-recovery.
        try:
            from core.config import project_root as _proot   # noqa: PLC0415
            own_home = str(_proot(candidate_pid).resolve())
        except Exception:
            own_home = None
        is_own_home = own_home is not None and str(source_dir) == own_home
        if candidate_pid in existing and not is_own_home:
            new_pid = _gen_fresh_pid()
            while new_pid in existing:
                new_pid = _gen_fresh_pid()
            report.renamed_from_pid = candidate_pid
            report.warnings.append(
                f"PID collision: {candidate_pid} already in registry; renamed to {new_pid}"
            )
            pid = new_pid
            report.pid = new_pid
            # Rewrite project.json pid + registry.id on disk so future loads
            # see the new identity (next backfill would do this anyway, but
            # be eager — saves one mismatched-state round trip).
            project_meta["pid"] = new_pid
            reg = project_meta.get("registry") or {}
            if isinstance(reg, dict):
                reg["id"] = new_pid
                project_meta["registry"] = reg
            try:
                project_json_path.write_text(json.dumps(project_meta))
            except Exception as e:
                report.warnings.append(f"could not rewrite project.json: {e}")
            # If the source dir name doesn't match the new pid, move it. The
            # parent dir is the runtime/projects/ root; we want a sibling
            # under the same parent named after new_pid.
            if source_dir.name != new_pid:
                new_dir = source_dir.parent / new_pid
                if not new_dir.exists():
                    try:
                        shutil.move(str(source_dir), str(new_dir))
                        source_dir = new_dir
                        project_json_path = source_dir / "project.json"
                    except Exception as e:
                        report.warnings.append(f"directory rename {source_dir}→{new_dir} failed: {e}")

    if dry_run:
        tdb = Path(tempfile.mkstemp(prefix="aba_recover_dry_", suffix=".db")[1])
    elif target_db is not None:
        tdb = Path(target_db)
    else:
        tdb = source_dir / "project.db"
    tdb.parent.mkdir(parents=True, exist_ok=True)
    report.target_db = str(tdb)

    # If we're rebuilding in-place, wipe any half-written DB first.
    if dry_run and tdb.exists():
        tdb.unlink()

    # Init schema. Defer import — the recovery module shouldn't pull the
    # whole core import graph just by existing. We must temporarily repoint
    # the global DB pointer to call init_db() on tdb, but the original is
    # restored immediately so dry-runs from a live process (drift detector)
    # don't pollute the caller's connection (P5 found this regression).
    from core.graph import _schema as _sm        # noqa: PLC0415
    prev_db_path = _sm.DB_PATH
    try:
        _sm.set_db_path(tdb)
        _sm.init_db()
    finally:
        _sm.set_db_path(prev_db_path)

    # Resolve source/target project dirs for I1 path normalization. The source
    # dir is what we're reading from; the target dir is where the rebuilt
    # project should live (project_root(pid) in the current runtime). When
    # the two match (same-host recovery), normalization is a no-op.
    src_proot = project_meta.get("source_project_dir") or str(source_dir)
    try:
        from core.config import project_root  # noqa: PLC0415
        tgt_proot = str(project_root(pid))
    except Exception:
        tgt_proot = str(source_dir)
    if src_proot != tgt_proot:
        report.warnings.append(
            f"cross-host import: rewriting absolute paths {src_proot} → {tgt_proot}"
        )

    c = sqlite3.connect(tdb)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN")

        # 1) entities
        ent_dir = source_dir / "entities"
        if ent_dir.is_dir():
            for f in sorted(ent_dir.glob("*.json")):
                payload = _read_json_safe(f)
                if not payload or not payload.get("id"):
                    report.warnings.append(f"unparseable entity sidecar: {f.name}")
                    continue
                try:
                    _insert_entity(c, payload, src_proot=src_proot, tgt_proot=tgt_proot)
                    report.entities += 1
                except sqlite3.DatabaseError as e:
                    report.warnings.append(f"entity insert failed {f.name}: {e}")

        # 2) execs — recursively walk for **/.exec/*.json
        for f in source_dir.rglob(".exec/*.json"):
            payload = _read_json_safe(f)
            if not payload or not payload.get("exec_id"):
                report.warnings.append(f"unparseable exec sidecar: {f.name}")
                continue
            try:
                _insert_exec(c, payload, f, src_proot=src_proot, tgt_proot=tgt_proot)
                report.execs += 1
            except sqlite3.DatabaseError as e:
                report.warnings.append(f"exec insert failed {f.name}: {e}")

        # 3) edges — read newest snapshot (if any), then the live tail.
        snapshots = sorted(source_dir.glob("edges-snapshot-*.jsonl"))
        if snapshots:
            # Use only the newest snapshot; older ones are GC candidates.
            snap = snapshots[-1]
            report.edge_snapshots_read = 1
            edges_seen = 0
            for row in _iter_jsonl(snap):
                op = row.get("op")
                if op == "add" and row.get("src") and row.get("dst"):
                    _apply_edge_add(c, row); report.edges_applied += 1; edges_seen += 1
                elif op == "remove":
                    _apply_edge_remove(c, row); report.edges_removed += 1
                else:
                    report.edge_lines_skipped += 1
        # Tail
        edges_tail = source_dir / "edges.jsonl"
        for row in _iter_jsonl(edges_tail):
            op = row.get("op")
            if op == "add" and row.get("src") and row.get("dst"):
                _apply_edge_add(c, row); report.edges_applied += 1
            elif op == "remove":
                _apply_edge_remove(c, row); report.edges_removed += 1
            else:
                report.edge_lines_skipped += 1

        # 4) messages — per-thread file; honor `clear` sentinels by deleting
        #    messages with entity_id from the *clear* op (thread-scoped if tid is set).
        threads_dir = source_dir / "threads"
        if threads_dir.is_dir():
            for f in sorted(threads_dir.glob("*.jsonl")):
                for row in _iter_jsonl(f):
                    if row.get("op") == "clear":
                        # clear-event: delete prior messages from THIS entity scope.
                        ent = row.get("entity_id") or "workspace"
                        tid = row.get("thread_id")
                        if tid is None:
                            c.execute("DELETE FROM messages WHERE entity_id = ?", (ent,))
                        else:
                            c.execute(
                                "DELETE FROM messages WHERE entity_id = ? AND thread_id = ?",
                                (ent, tid),
                            )
                        report.message_clears += 1
                        continue
                    try:
                        _insert_message(c, row)
                        report.messages += 1
                    except sqlite3.DatabaseError as e:
                        report.warnings.append(f"message insert failed in {f.name}: {e}")

        # 5) artifact count (informational — we don't move blobs).
        adir = source_dir / "artifacts"
        if adir.is_dir():
            report.artifacts_seen = sum(1 for _ in adir.rglob("*") if _.is_file())

        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    finally:
        c.close()

    # If we wrote a real DB, also update the workspace-level registry so the
    # project shows up on Home. (Skipped for dry_run.)
    if not dry_run and project_meta.get("registry"):
        try:
            from core import projects as _projects  # noqa: PLC0415
            # If we renamed via collision, the registry entry's id must match
            # the new pid (project.json was already rewritten earlier).
            reg_row = dict(project_meta["registry"])
            reg_row["id"] = pid
            _projects._save([
                p for p in _projects._load() if p["id"] != pid
            ] + [reg_row])
        except Exception as e:
            report.warnings.append(f"registry update failed: {e}")

    # I3 — compatibility report: write recovery_report.json into the project
    # dir. Skipped for dry_run (the drift detector calls build_report directly
    # against its temp DB if it wants the data).
    if not dry_run:
        try:
            from core.recovery.report import build_report  # noqa: PLC0415
            crep = build_report(source_dir, pid=pid, db_path=Path(tdb))
            # I4 — surface an UNAMBIGUOUS env-portability problem in the visible
            # warnings (the full detail rides recovery_report.json's
            # env_registry block). Named isolated envs whose EnvID isn't in this
            # deployment's weft store are gone (their locks travelled as a
            # pointer only); the default sessions self-heal, so they aren't
            # flagged. Absence of the registry file is ambiguous (no custom envs
            # vs lost) → left to the report, not the banner.
            if crep.env_named_unrecoverable:
                report.warnings.append(
                    "env registry: named env(s) not recoverable in this "
                    f"deployment's compute store: {sorted(crep.env_named_unrecoverable)} "
                    "— their locks didn't travel; recreate with make_isolated_env")
        except Exception as e:
            report.warnings.append(f"compatibility report failed: {e}")
        # R4 — refresh by-title symlinks from the just-imported entities.
        # The by-title dirs aren't part of the recovery archive (pure
        # derivation), so they need rebuild on import.
        try:
            from core.recovery.by_title import (   # noqa: PLC0415
                refresh_by_title_links, refresh_project_link_at_root,
            )
            refresh_by_title_links(source_dir)
            refresh_project_link_at_root(source_dir)
        except Exception as e:
            report.warnings.append(f"by-title refresh failed: {e}")

    return report


# ─── archived-tail GC (P4) ──────────────────────────────────────────────────
def gc_archived_edges(project_dir: Path, *, keep: int = 1) -> dict:
    """Delete `edges.jsonl.<seq>.archived` files older than the newest
    `keep` snapshots. Recovery only needs newest snapshot + live tail; older
    archives are dead weight. Returns {deleted, kept} counts."""
    project_dir = Path(project_dir).resolve()
    archived = sorted(project_dir.glob("edges.jsonl.*.archived"))
    if len(archived) <= keep:
        return {"deleted": 0, "kept": len(archived)}
    to_delete = archived[:-keep] if keep > 0 else archived
    deleted = 0
    for f in to_delete:
        try:
            f.unlink()
            deleted += 1
        except Exception:
            pass
    return {"deleted": deleted, "kept": len(archived) - deleted}


# ─── backfill (DB → FS) ─────────────────────────────────────────────────────
def backfill_project(project_dir: Path) -> RecoverReport:
    """Rewrite every sidecar + log from the live DB. Inverse direction of
    recover_project. Used to repair drift after a missed-hook bug is fixed
    (misc/recovery.md § 10.1)."""
    project_dir = Path(project_dir).resolve()
    pid = project_dir.name
    db_file = project_dir / "project.db"
    report = RecoverReport(pid=pid, source_dir=str(project_dir), target_db=str(db_file))
    if not db_file.exists():
        report.warnings.append(f"no live DB at {db_file}")
        return report

    # Construct a fresh Scribe writing into THIS pid's project dir.
    from core.recovery.scribe import (   # noqa: PLC0415
        Scribe, set_scribe_override,
        EntityUpserted, EdgeOp, MessageAppended, ProjectMetaChanged,
    )
    scribe = Scribe(tick_interval=10_000.0)
    prev_override = None
    try:
        # Stash any active override so backfill doesn't fight a test harness.
        import core.recovery.scribe as _sm   # noqa: PLC0415
        prev_override = _sm._SCRIBE_OVERRIDE
        set_scribe_override(scribe)

        c = sqlite3.connect(db_file)
        c.row_factory = sqlite3.Row

        # 1) project meta
        try:
            from core import projects as _projects   # noqa: PLC0415
            row = next((p for p in _projects._load() if p["id"] == pid), None)
            ws = c.execute("SELECT * FROM entities WHERE id='workspace'").fetchone()
            ws_dict = {k: ws[k] for k in ws.keys()} if ws else None
            if row:
                scribe.enqueue(ProjectMetaChanged(pid=pid, payload={
                    "registry": row, "project_entity": ws_dict,
                }))
        except Exception as e:
            report.warnings.append(f"project meta backfill skipped: {e}")

        # 2) entities
        for r in c.execute("SELECT * FROM entities").fetchall():
            row = {k: r[k] for k in r.keys()}
            scribe.enqueue(EntityUpserted(pid=pid, entity_id=row["id"], row=row))
            report.entities += 1

        # 3) edges (re-emit as `add` ops in row-id order)
        for r in c.execute("SELECT * FROM entity_edges ORDER BY id"):
            meta = r["metadata"]
            try:
                meta = json.loads(meta) if meta else None
            except Exception:
                pass
            scribe.enqueue(EdgeOp(
                pid=pid, op="add",
                src=r["source_id"], dst=r["target_id"], rel=r["rel_type"],
                meta=meta,
            ))
            report.edges_applied += 1

        # 4) messages
        for r in c.execute("SELECT * FROM messages ORDER BY id").fetchall():
            row = {k: r[k] for k in r.keys()}
            try:
                row["content"] = json.loads(row["content"]) if row["content"] else []
            except Exception:
                pass
            scribe.enqueue(MessageAppended(pid=pid, row=row))
            report.messages += 1

        c.close()
        # Drain everything to disk.
        scribe.flush()
    finally:
        # Restore prior override (if any) instead of clearing — tests may
        # be holding their own.
        set_scribe_override(prev_override)

    return report
