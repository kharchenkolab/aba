"""The output address index — where a run's outputs live, written when learned.

One table per project DB: `(run_id, rel) → {name, site, target, kind, bytes,
mtime, state, ref, sha256, source}`. It exists because ABA kept answering
"where is <name>?" by SEARCHING — iterating weft inventories per candidate run
at read time — when every fact in the row was in hand at WRITE time: the keep
decision attributes rels to targets, the terminal receipt carries site and
sizes, the harvest knows what it copied. A search that got capped for cost then
answered a click with 404 for a store that existed (live 2026-08-08; see
misc/addressing_and_the_missing_index.md). The catalog's job is to write
addresses down when it learns them, not to ask the courier to search the
warehouse per request.

Division of responsibility (deliberate, mirrors weft's own doc): weft indexes
container→contents (inventories) and identity→location (its location table);
ABA owns meaning→address — THIS table. Nothing here claims bytes exist: rows
are KNOWLEDGE, exactly like weft's receipts ("lose bytes, never knowledge").
Locations are hints validated by the layer that serves; a stale row costs a
re-derive, never a wrong byte.

Columns `ref`/`sha256` are nullable adapters for weft's trajectory: receipts
grow opt-in small-file hashes (`--hash-under`), and the Fabric epilogue will
hash outputs into DataRefs at the site — when a manifest or a mint provides
identity, `fill_ref` records it on the existing row; ABA never hashes.

Plane note: this module is core/graph (the sanctioned `_conn` home — the
store-port ratchet). It knows runs, rels, sites, targets — never file formats
or domain vocabulary. WHAT gets recorded (suffix policy, keep decisions) is
the content plane's call, at the hooks in content/bio/lifecycle/runs.py.
"""
from __future__ import annotations

import time
from typing import Optional

from ._schema import _conn

_DDL = """
CREATE TABLE IF NOT EXISTS output_addr (
    run_id      TEXT NOT NULL,
    rel         TEXT NOT NULL,
    name        TEXT NOT NULL,
    site        TEXT,
    target      TEXT,
    kind        TEXT,
    bytes       INTEGER,
    mtime       INTEGER,
    state       TEXT,
    ref         TEXT,
    sha256      TEXT,
    source      TEXT,
    recorded_at REAL,
    PRIMARY KEY (run_id, rel)
);
"""
_IDX = "CREATE INDEX IF NOT EXISTS idx_output_addr_name ON output_addr(name);"

_COLS = ("run_id", "rel", "name", "site", "target", "kind", "bytes", "mtime",
         "state", "ref", "sha256", "source", "recorded_at")


def _ensure(c) -> None:
    # Lazily per connection: existing project DBs predate the table, and a
    # lookup on a project that never recorded must not require a migration.
    c.execute(_DDL)
    c.execute(_IDX)


def record(rows: list) -> int:
    """Upsert rows by `(run_id, rel)`. Each row is a dict with at least
    `run_id`, `rel`, `name`; other columns optional. An upsert keeps the newest
    non-null facts: re-recording after a state change (saving → retained)
    refreshes; a sparser later row must not erase an earlier `ref`/`sha256`
    (COALESCE keeps the old value when the new is NULL). Returns rows written.
    Never raises — recording is best-effort by contract everywhere it is
    hooked (retention must never break a turn)."""
    out = 0
    try:
        with _conn() as c:
            _ensure(c)
            for r in rows:
                run_id, rel = r.get("run_id"), r.get("rel")
                name = r.get("name") or (str(rel).rstrip("/").rsplit("/", 1)[-1]
                                         if rel else None)
                if not run_id or not rel or not name:
                    continue
                vals = {k: r.get(k) for k in _COLS}
                vals.update(run_id=run_id, rel=rel, name=name,
                            recorded_at=r.get("recorded_at") or time.time())
                c.execute(
                    f"""INSERT INTO output_addr ({",".join(_COLS)})
                        VALUES ({",".join("?" for _ in _COLS)})
                        ON CONFLICT(run_id, rel) DO UPDATE SET
                          site=COALESCE(excluded.site, output_addr.site),
                          target=COALESCE(excluded.target, output_addr.target),
                          kind=COALESCE(excluded.kind, output_addr.kind),
                          bytes=COALESCE(excluded.bytes, output_addr.bytes),
                          mtime=COALESCE(excluded.mtime, output_addr.mtime),
                          state=COALESCE(excluded.state, output_addr.state),
                          ref=COALESCE(excluded.ref, output_addr.ref),
                          sha256=COALESCE(excluded.sha256, output_addr.sha256),
                          source=excluded.source,
                          recorded_at=excluded.recorded_at""",
                    tuple(vals[k] for k in _COLS))
                out += 1
    except Exception:  # noqa: BLE001 — knowledge recording must never break the caller
        return out
    return out


def by_name(name: str) -> list:
    """Rows whose recorded `name` or `rel` equals `name` (or its basename —
    callers hand in anything path-shaped). Newest first. Never raises."""
    base = str(name).rstrip("/").rsplit("/", 1)[-1]
    if not base:
        return []
    try:
        with _conn() as c:                     # row_factory is sqlite3.Row
            _ensure(c)
            rows = c.execute(
                "SELECT * FROM output_addr WHERE name=? OR rel=? "
                "ORDER BY recorded_at DESC", (base, base)).fetchall()
            return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def by_run(run_id: str) -> list:
    try:
        with _conn() as c:
            _ensure(c)
            rows = c.execute(
                "SELECT * FROM output_addr WHERE run_id=? ORDER BY rel",
                (run_id,)).fetchall()
            return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def fill_ref(run_id: str, rel: str, *, ref: Optional[str] = None,
             sha256: Optional[str] = None) -> None:
    """Attach identity to an existing row — called when a mint/receipt/manifest
    pays for it (data_register, resolve_run_file, hashed receipts). Never
    creates a row: identity without an address is not knowledge this table can
    serve. Never raises."""
    if not (ref or sha256):
        return
    try:
        with _conn() as c:
            _ensure(c)
            c.execute(
                "UPDATE output_addr SET ref=COALESCE(?, ref), "
                "sha256=COALESCE(?, sha256) WHERE run_id=? AND rel=?",
                (ref, sha256, run_id, rel))
    except Exception:  # noqa: BLE001
        pass
