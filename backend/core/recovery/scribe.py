"""Filesystem mirror for project state. See misc/recovery.md.

Every entity/edge/message/project mutation in core/graph/* enqueues an event
here. A background thread drains the queue every ~1 s and writes per-project
sidecars + jsonl logs under projects/<pid>/. The DB stays authoritative; the
scribe just mirrors. On disaster, aba-recover walks these files to rebuild a
project.

Design notes
------------
- One singleton scribe per process. Events carry pid; the scribe writes to the
  correct project dir.
- Plain threading.Thread (not asyncio) so it works from any caller — most
  hook sites are sync sqlite3 paths.
- No fsync, no tempfile+rename. The "rare recovery accepts up to a few seconds
  of loss" stance buys simplicity (recovery.md § 7).
- Coalescing: per-(pid, entity_id) for EntityUpserted; per-pid for
  ProjectMetaChanged; edge + message events are not coalesced (order matters).
- Per-project monotonic seq for edge ops; persisted in
  <project>/.scribe/state.json. Loaded lazily, written once per tick that
  touched edges.

Kill switch: set ABA_RECOVERY_DISABLED=1 to bypass writes entirely (used by
tests + offline tools that don't want the background thread).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


# ─── Event types ────────────────────────────────────────────────────────────
@dataclass
class EntityUpserted:
    pid: str
    entity_id: str
    row: dict  # full entity row dict from the DB


@dataclass
class EntityHardDeleted:
    pid: str
    entity_id: str


@dataclass
class EdgeOp:
    pid: str
    op: str  # "add" | "remove"
    src: str
    dst: str
    rel: str
    meta: Optional[dict] = None


@dataclass
class MessageAppended:
    pid: str
    row: dict  # full message dict (id, role, content, thread_id, ts, ...)


@dataclass
class MessagesCleared:
    pid: str
    entity_id: str
    thread_id: Optional[str]


@dataclass
class ProjectMetaChanged:
    pid: str
    payload: dict  # arbitrary project-level state (registry row, project entity, …)


# ─── Helpers ────────────────────────────────────────────────────────────────
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_FINGERPRINT_CACHE: Optional[tuple[str, str]] = None


def _aba_fingerprint() -> tuple[str, str]:
    """(commit, version). Probed once + cached for the process lifetime.
    Used to stamp project.json so cross-host import can produce precise
    version-skew warnings (recovery.md § 14)."""
    global _FINGERPRINT_CACHE
    if _FINGERPRINT_CACHE is not None:
        return _FINGERPRINT_CACHE
    commit = "unknown"
    try:
        # repo root = .../backend/core/recovery/scribe.py → up 4
        repo_root = Path(__file__).resolve().parents[3]
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, check=False, timeout=5,
        )
        if r.returncode == 0:
            commit = r.stdout.strip()
    except Exception:
        pass
    version = os.environ.get("ABA_VERSION", "dev")
    _FINGERPRINT_CACHE = (commit, version)
    return _FINGERPRINT_CACHE


def _project_root(pid: str) -> Path:
    """Resolve via core.config (deferred import to break a potential cycle)."""
    from core.config import project_root  # noqa: PLC0415
    return project_root(pid)


def _normalize_row(row: dict) -> dict:
    """Re-decode JSON-in-TEXT columns so sidecars hold real JSON objects
    (instead of stringified ones). The DB stores `metadata` and
    `producing_params` as TEXT; the scribe stores them as nested JSON for
    direct human/grep readability."""
    out = dict(row)
    for k in ("metadata", "producing_params"):
        v = out.get(k)
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except Exception:
                pass
    return out


# ─── The scribe ─────────────────────────────────────────────────────────────
class Scribe:
    """Background thread that drains an event queue and writes per-project
    sidecars + jsonl logs. Construct one per process (the module-level
    singleton in get_scribe()) or many for tests."""

    def __init__(self, *, tick_interval: float = 1.0, max_queue_size: int = 10_000,
                 compact_threshold_bytes: int = 16 * 1024 * 1024,
                 compact_check_every_ticks: int = 100):
        self._q: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._tick_interval = tick_interval
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Per-project monotonic edge seq counter; loaded lazily.
        self._seqs: dict[str, int] = {}
        self._seqs_lock = threading.Lock()
        # Tick counter — P4 keys compaction off this.
        self._tick_count = 0
        # P4 — compaction parameters.
        self._compact_threshold = int(compact_threshold_bytes)
        self._compact_every = int(compact_check_every_ticks)
        # Track all pids we've written to so compaction has somewhere to look.
        self._touched_pids: set[str] = set()

    # ─── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="aba-scribe")
        self._thread.start()

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        if drain:
            self._drain()
        self._stop.set()
        self._wakeup.set()
        self._thread.join(timeout=timeout)
        self._thread = None

    def flush(self) -> None:
        """Force a synchronous drain. For tests + clean shutdown."""
        self._drain()

    # ─── enqueue ────────────────────────────────────────────────────────────
    def enqueue(self, event) -> None:
        """Non-blocking enqueue from any thread. On overflow: one synchronous
        drain + retry; if still full, drop with a warning (we are not the
        durability path — the DB is)."""
        try:
            self._q.put_nowait(event)
        except queue.Full:
            self._drain()
            try:
                self._q.put_nowait(event)
            except queue.Full:
                _log.warning("scribe queue still full after drain — dropping event %r", event)

    # ─── internals ──────────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait(self._tick_interval)
            self._wakeup.clear()
            if self._stop.is_set():
                break
            try:
                self._tick_count += 1
                self._drain()
                # P4 — every Nth tick (~ once per 100 s of activity), check
                # edge log size and compact if over threshold. Synchronously
                # in-band; rare (only fires when log actually grew).
                if self._tick_count % self._compact_every == 0:
                    self._maybe_compact_edges()
            except Exception:
                _log.exception("scribe tick failed")

    def _drain(self) -> None:
        events: list = []
        while True:
            try:
                events.append(self._q.get_nowait())
            except queue.Empty:
                break
        if not events:
            return
        # Group events for batched / coalesced writes.
        entity_writes: dict[tuple[str, str], Optional[dict]] = {}  # (pid, eid) → row | None
        edge_batches: dict[str, list[dict]] = {}                   # pid → list of jsonl dicts
        message_batches: dict[tuple[str, Optional[str]], list[dict]] = {}  # (pid, tid) → lines
        project_writes: dict[str, dict] = {}                       # pid → payload

        for ev in events:
            if isinstance(ev, EntityUpserted):
                entity_writes[(ev.pid, ev.entity_id)] = ev.row  # last-write-wins coalesces
            elif isinstance(ev, EntityHardDeleted):
                entity_writes[(ev.pid, ev.entity_id)] = None
            elif isinstance(ev, EdgeOp):
                seq = self._next_seq(ev.pid)
                edge_batches.setdefault(ev.pid, []).append({
                    "_v": 1, "op": ev.op, "src": ev.src, "dst": ev.dst,
                    "rel": ev.rel, "meta": ev.meta, "seq": seq, "ts": _utcnow_iso(),
                })
            elif isinstance(ev, MessageAppended):
                tid = ev.row.get("thread_id")
                message_batches.setdefault((ev.pid, tid), []).append({"_v": 1, **ev.row})
            elif isinstance(ev, MessagesCleared):
                message_batches.setdefault((ev.pid, ev.thread_id), []).append({
                    "_v": 1, "op": "clear",
                    "entity_id": ev.entity_id, "thread_id": ev.thread_id,
                    "ts": _utcnow_iso(),
                })
            elif isinstance(ev, ProjectMetaChanged):
                project_writes[ev.pid] = ev.payload  # last-write-wins
            else:
                _log.warning("scribe: unknown event type %r", ev)

        for (pid, eid), row in entity_writes.items():
            try:
                self._write_entity(pid, eid, row)
            except Exception:
                _log.exception("scribe: entity write failed (pid=%s, eid=%s)", pid, eid)
        for pid, lines in edge_batches.items():
            try:
                self._append_edges(pid, lines)
            except Exception:
                _log.exception("scribe: edge append failed (pid=%s)", pid)
        for (pid, tid), lines in message_batches.items():
            try:
                self._append_messages(pid, tid, lines)
            except Exception:
                _log.exception("scribe: message append failed (pid=%s, tid=%s)", pid, tid)
        for pid, payload in project_writes.items():
            try:
                self._write_project_meta(pid, payload)
            except Exception:
                _log.exception("scribe: project meta write failed (pid=%s)", pid)

        # Persist seq counters for any project whose edge log we touched.
        if edge_batches:
            self._persist_seqs(set(edge_batches.keys()))

    # ─── writers ────────────────────────────────────────────────────────────
    def _write_entity(self, pid: str, entity_id: str, row: Optional[dict]) -> None:
        proot = _project_root(pid)
        entities_dir = proot / "entities"
        entities_dir.mkdir(parents=True, exist_ok=True)
        sidecar = entities_dir / f"{entity_id}.json"
        if row is None:
            sidecar.unlink(missing_ok=True)
            return
        payload = {"_v": 1, "_ts": _utcnow_iso(), **_normalize_row(row)}
        sidecar.write_text(json.dumps(payload, default=str))

    def _append_edges(self, pid: str, lines: list[dict]) -> None:
        proot = _project_root(pid)
        proot.mkdir(parents=True, exist_ok=True)
        log = proot / "edges.jsonl"
        with log.open("a") as f:
            for d in lines:
                f.write(json.dumps(d, default=str) + "\n")
        self._touched_pids.add(pid)

    # ─── P4 — compaction ────────────────────────────────────────────────────
    def _maybe_compact_edges(self) -> None:
        """For every pid we've written to in this process, stat edges.jsonl;
        if over threshold, perform an in-band compaction. Synchronous in
        the tick — the operation is rare (only fires when the log has
        genuinely grown) and bounded by current edge-set size (~200 ms for
        100k edges)."""
        for pid in list(self._touched_pids):
            try:
                proot = _project_root(pid)
                log = proot / "edges.jsonl"
                if not log.exists():
                    continue
                if log.stat().st_size < self._compact_threshold:
                    continue
                self._compact_edges_for(pid, proot)
            except Exception:
                _log.exception("scribe: compaction failed (pid=%s)", pid)

    def _compact_edges_for(self, pid: str, proot: Path) -> None:
        """Query the live DB for current edges, write a snapshot, rotate the
        tail. Snapshot lines re-use the regular `add` shape — recovery
        replays them like any other entry."""
        # Find the project DB
        try:
            from core.config import project_db_path  # noqa: PLC0415
            db_file = project_db_path(pid)
        except Exception:
            return
        if not db_file.exists():
            return
        import sqlite3 as _sql  # noqa: PLC0415
        # Read current edge state
        c = _sql.connect(db_file)
        c.row_factory = _sql.Row
        try:
            rows = c.execute(
                "SELECT source_id, target_id, rel_type, metadata, created_at "
                "FROM entity_edges ORDER BY id"
            ).fetchall()
        finally:
            c.close()
        # Determine snapshot seq (== current persisted seq)
        with self._seqs_lock:
            seq = self._seqs.get(pid, self._load_seq(pid))
        # Write snapshot
        snap = proot / f"edges-snapshot-{seq}.jsonl"
        snap_tmp = snap.with_suffix(snap.suffix + ".tmp")
        with snap_tmp.open("w") as f:
            for r in rows:
                meta = r["metadata"]
                try:
                    meta = json.loads(meta) if meta else None
                except Exception:
                    pass
                f.write(json.dumps({
                    "_v": 1, "op": "add",
                    "src": r["source_id"], "dst": r["target_id"], "rel": r["rel_type"],
                    "meta": meta, "seq": seq, "ts": r["created_at"] or _utcnow_iso(),
                }, default=str) + "\n")
        snap_tmp.replace(snap)
        # Rotate tail (archive the old log; start a fresh empty one)
        log = proot / "edges.jsonl"
        archived = proot / f"edges.jsonl.{seq}.archived"
        try:
            log.replace(archived)
        except FileNotFoundError:
            pass

    def _append_messages(self, pid: str, thread_id: Optional[str], lines: list[dict]) -> None:
        proot = _project_root(pid)
        threads_dir = proot / "threads"
        threads_dir.mkdir(parents=True, exist_ok=True)
        # tids look like "thr_<hex>" or None → "default". Sanitize defensively.
        tid_safe = thread_id if thread_id else "default"
        tid_safe = "".join(c for c in tid_safe if c.isalnum() or c in "-_") or "default"
        log = threads_dir / f"{tid_safe}.jsonl"
        with log.open("a") as f:
            for d in lines:
                f.write(json.dumps(d, default=str) + "\n")

    def _write_project_meta(self, pid: str, payload: dict) -> None:
        proot = _project_root(pid)
        proot.mkdir(parents=True, exist_ok=True)
        pf = proot / "project.json"
        commit, version = _aba_fingerprint()
        out = {
            "_v": 1,
            "_ts": _utcnow_iso(),
            "aba_commit": commit,
            "aba_version": version,
            "pid": pid,
            # Stamp the absolute project dir so cross-host import (recovery.md
            # § 14 / I1) can compute the path-prefix substitution without
            # having to guess. Same-host imports compare this to the target
            # project_root and skip normalization if equal.
            "source_project_dir": str(proot),
            **payload,
        }
        pf.write_text(json.dumps(out, default=str))

    # ─── seq counter persistence ────────────────────────────────────────────
    def _next_seq(self, pid: str) -> int:
        with self._seqs_lock:
            if pid not in self._seqs:
                self._seqs[pid] = self._load_seq(pid)
            self._seqs[pid] += 1
            return self._seqs[pid]

    def _load_seq(self, pid: str) -> int:
        proot = _project_root(pid)
        state_file = proot / ".scribe" / "state.json"
        if not state_file.exists():
            return 0
        try:
            d = json.loads(state_file.read_text())
            return int(d.get("last_edge_seq", 0))
        except Exception:
            return 0

    def _persist_seqs(self, pids: set[str]) -> None:
        with self._seqs_lock:
            for pid in pids:
                seq = self._seqs.get(pid, 0)
                proot = _project_root(pid)
                state_dir = proot / ".scribe"
                state_dir.mkdir(parents=True, exist_ok=True)
                (state_dir / "state.json").write_text(json.dumps({"last_edge_seq": seq}))


# ─── Singleton + test override ──────────────────────────────────────────────
class _NullScribe:
    """No-op scribe returned when ABA_RECOVERY_DISABLED=1 (offline tools,
    migration scripts). Drops every enqueue silently; never writes to disk."""
    def enqueue(self, event) -> None: pass
    def flush(self) -> None: pass
    def start(self) -> None: pass
    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None: pass


_SCRIBE: Optional[Scribe] = None
_SCRIBE_OVERRIDE = None  # type: Optional[object]
_SCRIBE_LOCK = threading.Lock()


def set_scribe_override(s) -> None:
    """For tests: replace the process-wide scribe with a custom instance.
    Pass None to clear. Lets tests drive ticks deterministically (construct
    a Scribe with a very large tick_interval, inject it, force flush() per
    step, restore None at teardown)."""
    global _SCRIBE_OVERRIDE
    _SCRIBE_OVERRIDE = s


def get_scribe():
    """Process-wide scribe. Resolution order:
      1. test override (set_scribe_override), if set
      2. _NullScribe, if ABA_RECOVERY_DISABLED=1
      3. lazily-constructed singleton (background thread starts on first call)
    """
    if _SCRIBE_OVERRIDE is not None:
        return _SCRIBE_OVERRIDE
    if disabled():
        return _NullScribe()
    global _SCRIBE
    if _SCRIBE is None:
        with _SCRIBE_LOCK:
            if _SCRIBE is None:
                _SCRIBE = Scribe()
                _SCRIBE.start()
    return _SCRIBE


def disabled() -> bool:
    return bool(os.environ.get("ABA_RECOVERY_DISABLED"))
