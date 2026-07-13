"""Execution records — provenance source-of-truth for tool calls that
produce artifacts (run_python / run_r in Stage 1; broader later).

See misc/exec_records_and_versioning.md for the full design. In a sentence:
every tool call that produces an artifact writes one **exec record**. The
record holds code + executor + env + I/O — everything needed to reproduce
the call. Entities reference the exec record by `exec_id`; producing_code
no longer lives on entity rows.

Storage:
  - Thin DB row in execution_records (an index for fast list/lookup).
  - Full JSON sidecar at <exec_dir>/.exec/<exec_id>.json colocated with
    the workdir. `record_path` in the DB row points at it absolutely.

`exec_dir` is the workdir of the active Run if there is one, else the
thread's scratch dir. Callers don't have to know which — they pass the
cwd that the kernel was just executing in (run_python / run_r already
resolves this via _run_scratch_cwd), and create() uses it as the
parent dir for `.exec/`.
"""
from __future__ import annotations
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from core import config
from core.graph._schema import _conn, _utcnow

_log = logging.getLogger(__name__)


def gen_exec_id() -> str:
    return f"exec_{uuid.uuid4().hex[:12]}"


def record_path_for(cwd: str | Path, exec_id: str) -> Path:
    """Resolve the JSON sidecar path for an exec_id given the kernel cwd
    (Run output dir, or thread scratch dir). Creates `.exec/` if missing.
    """
    p = Path(cwd) / ".exec"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{exec_id}.json"


def create(
    *,
    exec_id: Optional[str] = None,
    thread_id: str,
    run_id: Optional[str] = None,
    tool_use_id: Optional[str] = None,
    tool_name: str,
    status: str,
    code: str = "",
    code_hash: Optional[str] = None,
    started_at: str,
    completed_at: Optional[str] = None,
    record_path: Optional[str | Path] = None,
    cwd: Optional[str | Path] = None,
    payload: Optional[dict] = None,
) -> str:
    """Create an exec record: write the JSON sidecar AND the DB index row.

    Either `record_path` or `cwd` must be provided so the sidecar location
    is determinate. If only `cwd` is given, the path is derived via
    record_path_for(cwd, exec_id).

    `payload` is the rest of the JSON body (executor, language, package_versions,
    used, produced, stdout_tail, stderr_tail, etc.). The function adds the
    skeleton fields (exec_id, thread_id, run_id, tool_use_id, tool_name,
    status, code, code_hash, started_at, completed_at) on top.

    Returns the exec_id (generated if not supplied).

    Failure mode: if the JSON write fails we don't insert the DB row.
    If the DB insert fails we attempt to remove the sidecar. This keeps
    the index ↔ filesystem consistent.
    """
    if not thread_id:
        raise ValueError("exec_records.create: thread_id is required")
    if not tool_name:
        raise ValueError("exec_records.create: tool_name is required")
    if not status:
        raise ValueError("exec_records.create: status is required")
    eid = exec_id or gen_exec_id()
    if record_path is None:
        if cwd is None:
            raise ValueError("exec_records.create: either record_path or cwd required")
        record_path = record_path_for(cwd, eid)
    rp = Path(record_path)

    body = {
        "exec_id":       eid,
        "thread_id":     thread_id,
        "run_id":        run_id,
        "tool_use_id":   tool_use_id,
        "tool_name":     tool_name,
        "status":        status,
        "code":          code,
        "code_hash":     code_hash,
        "started_at":    started_at,
        "completed_at":  completed_at,
    }
    if payload:
        # Merge with payload taking precedence for non-skeleton keys; skeleton
        # keys above stay authoritative.
        for k, v in payload.items():
            if k not in body:
                body[k] = v

    # Dedup the env manifest (provenance.md §3.1): the package list is identical
    # across the many runs sharing an env. Store it once content-addressed by
    # env_fingerprint and drop it from the inline body; get() re-inflates it
    # transparently. If the store write fails, keep it inline (no data lost).
    if body.get("package_versions") and body.get("env_fingerprint"):
        try:
            from core.exec.env_manifest import store as _store_manifest
            if _store_manifest(body["env_fingerprint"], body["package_versions"],
                               body.get("language_version", "")):
                body.pop("package_versions", None)
        except Exception:  # noqa: BLE001
            pass

    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")

    try:
        with _conn() as c:
            c.execute(
                """INSERT INTO execution_records
                   (exec_id, thread_id, run_id, tool_use_id, tool_name,
                    status, code_hash, record_path, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (eid, thread_id, run_id, tool_use_id, tool_name,
                 status, code_hash, str(rp), started_at, completed_at),
            )
            c.commit()
    except Exception:
        # Roll back the sidecar so we don't leak a JSON file with no DB row.
        try:
            rp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return eid


def attach_to_run(exec_id: str, run_id: str) -> bool:
    """Backfill the `run_id` on an existing exec record (both the DB index
    row and the JSON sidecar). Used when an ambient analysis materializes
    AFTER its first child exec ran — that exec's record initially has
    run_id=NULL (scratch), and we want artifacts_for_run(ambient_id) to
    surface it once the ambient is created.

    Returns True if the record was found and updated. Idempotent.
    """
    if not exec_id or not run_id:
        return False
    with _conn() as c:
        r = c.execute(
            "SELECT record_path, run_id FROM execution_records WHERE exec_id = ?",
            (exec_id,),
        ).fetchone()
        if not r:
            return False
        if r["run_id"] == run_id:
            return True  # already attached, no-op
        c.execute("UPDATE execution_records SET run_id = ? WHERE exec_id = ?",
                  (run_id, exec_id))
        c.commit()
    # Update the JSON sidecar too so `get(exec_id)` returns consistent data.
    try:
        rp = Path(r["record_path"])
        body = json.loads(rp.read_text(encoding="utf-8"))
        body["run_id"] = run_id
        rp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as e:
        _log.warning(
            "attach_to_run: sidecar rewrite failed for %s at %s: %s",
            exec_id, r["record_path"], e,
        )
    return True


def latest_exec_id_for_thread(thread_id: str) -> Optional[str]:
    """The most recent exec record for a thread (by start time). Used to auto-link a
    just-registered dataset to the run that fetched/created it (misc/provenance.md), so
    its provenance surfaces the fetch code + env. None if the thread has no exec records."""
    if not thread_id:
        return None
    try:
        with _conn() as c:
            r = c.execute(
                "SELECT exec_id FROM execution_records WHERE thread_id = ? "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1", (thread_id,)).fetchone()
        return r["exec_id"] if r else None
    except Exception:  # noqa: BLE001 — a lookup failure just means no auto-link
        return None


def get(exec_id: str) -> Optional[dict]:
    """Fetch the full exec record (DB index + JSON body merged).

    Returns None if the exec_id is unknown. If the DB row exists but the
    JSON sidecar is missing (someone hand-deleted a workdir), returns the
    index fields only — the caller can detect the missing body by the
    absence of `code` / `produced` etc.
    """
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM execution_records WHERE exec_id = ?", (exec_id,)
        ).fetchone()
    if not r:
        return None
    out = {
        "exec_id":      r["exec_id"],
        "thread_id":    r["thread_id"],
        "run_id":       r["run_id"],
        "tool_use_id":  r["tool_use_id"],
        "tool_name":    r["tool_name"],
        "status":       r["status"],
        "code_hash":    r["code_hash"],
        "record_path":  r["record_path"],
        "started_at":   r["started_at"],
        "completed_at": r["completed_at"],
    }
    try:
        body = json.loads(Path(r["record_path"]).read_text(encoding="utf-8"))
        # Body overrides the slim DB view on overlapping keys; both contain
        # the same data normally, but the JSON has the rich fields too.
        for k, v in body.items():
            out[k] = v
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("exec_records.get: sidecar unreadable for %s at %s: %s",
                     exec_id, r["record_path"], e)
    # Re-inflate a deduped env manifest (provenance.md §3.1): records written
    # after the dedup keep only env_fingerprint inline — resolve the package
    # versions back so callers see them transparently.
    if out.get("env_fingerprint") and not out.get("package_versions"):
        try:
            from core.exec.env_manifest import load as _load_manifest
            m = _load_manifest(out["env_fingerprint"])
            if m.get("package_versions"):
                out["package_versions"] = m["package_versions"]
        except Exception:  # noqa: BLE001
            pass
    return out


def list_by_run(run_id: str, *, limit: Optional[int] = None) -> list[dict]:
    """Index entries (DB row only — no JSON hydration) for an entire Run."""
    if not run_id:
        return []
    q = "SELECT * FROM execution_records WHERE run_id = ? ORDER BY started_at"
    args: list = [run_id]
    if limit is not None:
        q += " LIMIT ?"
        args.append(int(limit))
    with _conn() as c:
        return [_row_index(r) for r in c.execute(q, args).fetchall()]


def list_by_thread(thread_id: str, *, run_id_filter: Optional[str] = None,
                   limit: Optional[int] = None) -> list[dict]:
    """Index entries for a thread. Pass run_id_filter='' to get scratch-only
    (rows with NULL run_id). Without it, every record in the thread (both
    Run-attributed and scratch) is returned.
    """
    if not thread_id:
        return []
    q = "SELECT * FROM execution_records WHERE thread_id = ?"
    args: list = [thread_id]
    if run_id_filter == "":
        q += " AND run_id IS NULL"
    elif run_id_filter is not None:
        q += " AND run_id = ?"
        args.append(run_id_filter)
    q += " ORDER BY started_at"
    if limit is not None:
        q += " LIMIT ?"
        args.append(int(limit))
    with _conn() as c:
        return [_row_index(r) for r in c.execute(q, args).fetchall()]


def lookup_code_for_entity(entity: dict | None) -> str:
    """Return the producing-code string for an entity.

    Post Cutover 4 (misc/exec_records_and_versioning.md): the exec record
    pointer (entity.exec_id) is the sole source. Legacy entities that had
    a producing_code column were backfilled into synthetic exec records
    by `backfill_legacy_producing_code` on init_db, so the column has
    been dropped.

    Returns "" when the entity has no exec_id, the exec record is missing,
    or its `code` field is empty. Never raises — UI/manifest code paths
    shouldn't fail just because an exec sidecar got hand-deleted.
    """
    if not entity:
        return ""
    eid = entity.get("exec_id")
    if not eid:
        return ""
    try:
        rec = get(eid)
    except Exception:  # noqa: BLE001 — best-effort lookup
        return ""
    return (rec or {}).get("code") or ""


def lookup_codes_for_entities(entities: list[dict]) -> dict[str, str]:
    """Bulk equivalent of lookup_code_for_entity — for hot paths (manifest
    assembler) that resolve code for many entities at once.

    One DB query to fetch every distinct exec_id's record_path, then one
    JSON read per distinct sidecar. Entities without exec_id resolve from
    their own producing_code without any DB/disk hit (legacy fast-path).

    Returns {entity_id: code}. Entities that have neither exec_id nor
    producing_code map to "" so callers don't need to None-check.
    """
    out: dict[str, str] = {}
    if not entities:
        return out
    # Partition: legacy (no exec_id) gets resolved from the entity dict;
    # exec-backed entities are grouped by exec_id for a single bulk query.
    exec_to_entities: dict[str, list[str]] = {}
    for e in entities:
        if not e:
            continue
        eid = e.get("id")
        if not eid:
            continue
        ex = e.get("exec_id")
        if ex:
            exec_to_entities.setdefault(ex, []).append(eid)
        else:
            out[eid] = e.get("producing_code") or ""

    if not exec_to_entities:
        return out

    exec_ids = list(exec_to_entities.keys())
    placeholders = ",".join("?" * len(exec_ids))
    with _conn() as c:
        rows = c.execute(
            f"SELECT exec_id, record_path FROM execution_records WHERE exec_id IN ({placeholders})",
            exec_ids,
        ).fetchall()
    path_by_exec = {r["exec_id"]: r["record_path"] for r in rows}
    code_by_exec: dict[str, str] = {}
    for ex in exec_ids:
        rp = path_by_exec.get(ex)
        if not rp:
            code_by_exec[ex] = ""
            continue
        try:
            body = json.loads(Path(rp).read_text(encoding="utf-8"))
            code_by_exec[ex] = body.get("code") or ""
        except (OSError, json.JSONDecodeError) as e:  # noqa: F841
            code_by_exec[ex] = ""

    # Fan out, falling back to legacy producing_code when the exec record
    # is missing or empty (mirrors the single-entity helper's behavior).
    legacy_by_id = {e.get("id"): (e.get("producing_code") or "")
                    for e in entities if e and e.get("id")}
    for ex, ent_ids in exec_to_entities.items():
        code = code_by_exec.get(ex, "")
        for eid in ent_ids:
            if code:
                out[eid] = code
            else:
                out[eid] = legacy_by_id.get(eid, "")
    return out


def aggregated_code_for_run(run_id: str, *,
                             separator: str = "\n\n# ---\n") -> str:
    """Concatenate every exec record's code for this Run, ordered by
    started_at, joined by `separator` (defaults to the `# ---` separator
    runs.py:232 used historically).

    Replaces the denormalized Run.producing_code aggregate that
    append_run_code maintained. Reads each exec record's JSON sidecar
    directly (cheaper than going through get() since we skip the rebuild
    of the index dict).

    Returns "" if the Run has no exec records (an empty Run, freshly
    opened with no tool calls yet).
    """
    if not run_id:
        return ""
    parts: list[str] = []
    for r in list_by_run(run_id):
        rp = r.get("record_path")
        if not rp:
            continue
        try:
            body = json.loads(Path(rp).read_text(encoding="utf-8"))
            code = body.get("code")
            if code:
                parts.append(code)
        except (OSError, json.JSONDecodeError):
            continue
    return separator.join(parts) if parts else ""


def backfill_legacy_producing_code(*, dry_run: bool = False) -> dict:
    """One-shot migration helper (idempotent): for every entity that has
    `producing_code` set but no `exec_id`, write a synthetic exec record
    so post-cutover code paths can drill through the new pointer.

    The synthetic record is a degraded reconstruction — no env_fingerprint,
    no package_versions, no produced[], no real started_at — but it's
    enough to satisfy lookup_code_for_entity, aggregated_code_for_run,
    and reproduce_from_exec. Synthetic records are flagged with
    `source: "backfill"` in their JSON body so downstream UI can warn
    users that env_drift comparisons are not meaningful for these.

    Idempotent: subsequent calls re-query and find no candidates (exec_id
    is already set on the previously-backfilled rows). Safe to call from
    init_db on every startup.

    Returns: {backfilled: N, scanned: M, skipped_no_code: K, errors: E}.

    With dry_run=True, performs only the scan and returns the count of
    would-be-backfilled entities without writing anything. Useful for
    operators evaluating the upgrade impact.
    """
    import hashlib
    from datetime import datetime, timezone
    log = logging.getLogger("exec_records.backfill")

    # Post-Cutover-4 fresh DBs don't have the column at all (it was
    # dropped on upgrade; never created on fresh). Detect that and skip
    # — the function becomes a no-op once the migration is done.
    with _conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(entities)").fetchall()}
        if "producing_code" not in cols:
            return {"backfilled": 0, "scanned": 0, "skipped_no_code": 0, "errors": 0}
        candidates = c.execute(
            "SELECT id, type, title, producing_code, created_at, metadata "
            "FROM entities WHERE producing_code IS NOT NULL "
            "AND producing_code != '' AND exec_id IS NULL"
        ).fetchall()
    if not candidates:
        return {"backfilled": 0, "scanned": 0, "skipped_no_code": 0, "errors": 0}

    backfill_root_env = str(config.RUNTIME_DIR)
    backfill_dir = Path(backfill_root_env) / "exec-backfill"
    if not dry_run:
        backfill_dir.mkdir(parents=True, exist_ok=True)

    backfilled = errors = skipped = 0
    for r in candidates:
        eid = r["id"]
        code = r["producing_code"]
        if not code or not code.strip():
            skipped += 1
            continue
        if dry_run:
            backfilled += 1
            continue

        try:
            # Sniff language from the code itself via the content-registered
            # "language_sniffer" service (R signals beat python; default python
            # on tie / when no pack is registered). Inverted off a direct bio
            # import to keep the platform/content seam (check_seam.sh).
            from core.services import call_service
            lang = call_service("language_sniffer", code, default="python")
            tool_name = "run_r" if lang == "r" else "run_python"

            # Pull thread_id off entity metadata if present so the row is
            # navigable per-thread, mirroring real exec records.
            meta = {}
            try:
                if r["metadata"]:
                    meta = json.loads(r["metadata"])
            except Exception:  # noqa: BLE001
                meta = {}
            tid = meta.get("thread_id") or "backfill"

            ex_id = f"exec_bf_{hashlib.sha256(eid.encode()).hexdigest()[:10]}"
            ts = r["created_at"] or datetime.now(timezone.utc).isoformat()

            ch = "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()
            rp = backfill_dir / f"{ex_id}.json"
            body = {
                "exec_id":      ex_id,
                "thread_id":    tid,
                "run_id":       None,
                "tool_use_id":  None,
                "tool_name":    tool_name,
                "status":       "ok",
                "code":         code,
                "code_hash":    ch,
                "started_at":   ts,
                "completed_at": ts,
                "executor":     f"backfill:{lang}",
                "language":     lang,
                "source":       "backfill",   # so the UI can flag degraded records
                "produced":     [],
                "stdout_tail":  "",
                "stderr_tail":  "",
            }
            rp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")

            with _conn() as c:
                c.execute(
                    """INSERT OR IGNORE INTO execution_records
                       (exec_id, thread_id, run_id, tool_use_id, tool_name,
                        status, code_hash, record_path, started_at, completed_at)
                       VALUES (?, ?, NULL, NULL, ?, 'ok', ?, ?, ?, ?)""",
                    (ex_id, tid, tool_name, ch, str(rp), ts, ts),
                )
                c.execute("UPDATE entities SET exec_id = ? WHERE id = ? AND exec_id IS NULL",
                          (ex_id, eid))
                c.commit()
            backfilled += 1
        except Exception as e:  # noqa: BLE001 — log and keep going; a bad row shouldn't stop the batch
            log.warning("backfill_legacy_producing_code: entity %s failed: %s", eid, e)
            errors += 1

    log.info(
        "backfill_legacy_producing_code: %s entities backfilled (scanned %d, "
        "skipped %d empty, %d errors)%s",
        backfilled, len(candidates), skipped, errors,
        " (dry-run)" if dry_run else "",
    )
    return {"backfilled": backfilled, "scanned": len(candidates),
            "skipped_no_code": skipped, "errors": errors}


def _row_index(r) -> dict:
    return {
        "exec_id":      r["exec_id"],
        "thread_id":    r["thread_id"],
        "run_id":       r["run_id"],
        "tool_use_id":  r["tool_use_id"],
        "tool_name":    r["tool_name"],
        "status":       r["status"],
        "code_hash":    r["code_hash"],
        "record_path":  r["record_path"],
        "started_at":   r["started_at"],
        "completed_at": r["completed_at"],
    }
