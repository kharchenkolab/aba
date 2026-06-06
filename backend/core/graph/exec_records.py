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
