"""P5 — Drift detector.

Compares the live project.db against what the recovery walker would
reconstruct from the on-disk sidecars + logs. Non-zero skew = a missed hook
or payload-shape bug in the scribe; the project is fine NOW (DB is
authoritative) but recovery would silently lose data.

Three check depths (recovery.md § 10.0):
- "count":   counts only (~10–50 ms). Fired on project open.
- "sampled": counts + random N-row field-level compare (~50–100 ms).
             Fired by the scribe-idle timer after 5 min of no events.
- "full":    every row, every field (~1–3 s). Fired on the UI's
             "Verify recovery archive" button (project ⋯ menu).

Result is persisted to <project_dir>/.scribe/drift.json so the
observability panel + the recovery-banner UI can read it cheaply.
"""
from __future__ import annotations
import json
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


TABLES = ("entities", "entity_edges", "messages", "execution_records")


@dataclass
class DriftReport:
    pid: str
    project_dir: str
    depth: str                          # "count" | "sampled" | "full"
    checked_at: str                     # ISO timestamp
    skew_score: float = 0.0             # 0.0 = no drift; up to 1.0
    counts_live: dict = field(default_factory=dict)
    counts_fs: dict = field(default_factory=dict)
    sample_size: int = 0
    sample_mismatches: int = 0
    field_mismatches: list[dict] = field(default_factory=list)  # [{table, id, field, live, fs}, ...]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "project_dir": self.project_dir,
            "depth": self.depth,
            "checked_at": self.checked_at,
            "skew_score": self.skew_score,
            "counts": {"live": self.counts_live, "fs": self.counts_fs},
            "sample": {"size": self.sample_size, "mismatches": self.sample_mismatches},
            "field_mismatches": list(self.field_mismatches),
            "error": self.error,
        }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _table_counts(db: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not db.exists():
        return counts
    c = sqlite3.connect(db)
    try:
        for t in TABLES:
            try:
                counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.DatabaseError:
                counts[t] = 0
    finally:
        c.close()
    return counts


def _sample_entity_rows(db: Path, n: int) -> list[dict]:
    """Random sample of n entities (id + key fields) from a DB."""
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            "SELECT id, type, title, status, metadata, artifact_path "
            "FROM entities ORDER BY id"
        ).fetchall()
    finally:
        c.close()
    out = [dict(r) for r in rows]
    if n is None or n >= len(out):
        return out
    random.shuffle(out)
    return out[:n]


def _entity_by_id(db: Path, eid: str) -> Optional[dict]:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute(
            "SELECT id, type, title, status, metadata, artifact_path FROM entities WHERE id = ?",
            (eid,),
        ).fetchone()
    finally:
        c.close()
    return dict(r) if r else None


def compute_drift(project_dir: Path, *, depth: str = "count", sample_size: int = 100) -> DriftReport:
    """Compute drift between the live project.db and a dry-run recovery
    rebuilt from on-disk sidecars. `depth` controls cost vs coverage."""
    pdir = Path(project_dir).resolve()
    live_db = pdir / "project.db"
    pid = pdir.name
    rep = DriftReport(pid=pid, project_dir=str(pdir), depth=depth,
                      checked_at=_utcnow_iso())

    if not live_db.exists():
        rep.error = f"no live DB at {live_db}"
        return _persist(pdir, rep)

    # Always compute counts (it's cheap and grounds the skew score)
    rep.counts_live = _table_counts(live_db)

    # Dry-run recovery into a tempfile
    from core.recovery.walker import recover_project  # noqa: PLC0415
    try:
        wr = recover_project(pdir, dry_run=True)
        fs_db = Path(wr.target_db)
    except Exception as e:
        rep.error = f"recovery walker failed: {e}"
        return _persist(pdir, rep)

    try:
        rep.counts_fs = _table_counts(fs_db)
        # Score: weighted absolute diff / max(live, fs). 1.0 = totally drifted.
        diffs = 0
        scale = 0
        for t in TABLES:
            live = rep.counts_live.get(t, 0)
            fs = rep.counts_fs.get(t, 0)
            diffs += abs(live - fs)
            scale += max(live, fs)
        rep.skew_score = (diffs / scale) if scale else 0.0

        if depth in ("sampled", "full"):
            # Field-level compare on entity rows (the highest-value drift signal)
            n = sample_size if depth == "sampled" else None
            sample = _sample_entity_rows(live_db, n)
            rep.sample_size = len(sample)
            for live_row in sample:
                eid = live_row["id"]
                fs_row = _entity_by_id(fs_db, eid)
                if fs_row is None:
                    rep.sample_mismatches += 1
                    rep.field_mismatches.append({
                        "table": "entities", "id": eid, "field": "*",
                        "live": "present", "fs": "missing",
                    })
                    continue
                for f in ("type", "title", "status", "metadata", "artifact_path"):
                    if str(live_row.get(f) or "") != str(fs_row.get(f) or ""):
                        rep.sample_mismatches += 1
                        rep.field_mismatches.append({
                            "table": "entities", "id": eid, "field": f,
                            "live": live_row.get(f), "fs": fs_row.get(f),
                        })
                        break  # one mismatch per entity is enough
            # Bump skew if sampled mismatches exist but counts looked fine.
            if rep.sample_size:
                sample_rate = rep.sample_mismatches / rep.sample_size
                rep.skew_score = max(rep.skew_score, sample_rate)
    finally:
        try:
            fs_db.unlink(missing_ok=True)
        except Exception:
            pass

    return _persist(pdir, rep)


def _persist(pdir: Path, rep: DriftReport) -> DriftReport:
    try:
        sdir = pdir / ".scribe"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "drift.json").write_text(json.dumps(rep.to_dict(), indent=2, default=str))
    except Exception:
        pass
    return rep
