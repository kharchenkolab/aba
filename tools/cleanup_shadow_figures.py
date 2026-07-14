"""Phase 7 of misc/exec_records_and_versioning.md: one-shot cleanup of
legacy shadow figure/table entities.

Before Option B's Phase 5 cutover, the registry pre-emptively minted a
figure entity for every harvested PNG and a table entity for every CSV.
Most were never pinned — they sat in the entities table as "shadow"
rows, filtered from the rails but eating DB space and complicating the
mental model. Post-cutover, they no longer get created, but the
existing ones linger.

This script finds and removes them.

Default mode is **dry run**: it reports what WOULD be deleted without
touching the DB. Pass `--apply` to actually delete.

A row is considered a shadow if ALL of these are true:
  - type ∈ {figure, table}
  - pinned = false (or 0/null)
  - status = 'active'  (we never touch archived rows; admin restoration
                        is the legit way to revive them)
  - no incoming edges from a Result (verifying nothing curated relies
    on it; if a Result includes this entity, it gets a pass)

We also leave figures with revisions alone (their chain might matter
to the user even if the leaf isn't pinned). Tables don't have
revisions in current usage so we skip that check for them.

Usage:
  .venv/bin/python tools/cleanup_shadow_figures.py            # dry run
  .venv/bin/python tools/cleanup_shadow_figures.py --apply    # actually delete
  .venv/bin/python tools/cleanup_shadow_figures.py --project p_abc --apply
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

# Make the backend importable.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up shadow figure/table entities (legacy pre-cutover)."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete the rows. Without this flag, runs in dry-run mode."
    )
    parser.add_argument(
        "--project", default=None,
        help="Project id (sets ABA_DB_PATH to that project's DB). Defaults to "
             "ABA_DB_PATH as configured in env."
    )
    args = parser.parse_args()

    if args.project:
        # Point the workspace DB at this project (SINGLE mode). Must be ABA_DB_PATH:
        # the former ABA_DB_PATH_OVERRIDE alias was merged away (env_reorg §6), so
        # setting it would be silently ignored and this tool would operate on the
        # DEFAULT workspace DB — deleting rows from the wrong database under --apply.
        from core.config import project_db_path
        os.environ["ABA_DB_PATH"] = str(project_db_path(args.project))

    from core.graph._schema import init_db, _conn
    init_db()

    with _conn() as c:
        candidates = c.execute("""
            SELECT id, type, title, exec_id, parent_entity_id, created_at
            FROM entities
            WHERE type IN ('figure', 'table')
              AND status = 'active'
              AND (pinned = 0 OR pinned IS NULL)
        """).fetchall()

    if not candidates:
        print("[shadow-cleanup] nothing to clean — no unpinned active figures/tables.")
        return 0

    # Filter out figures with revision edges (in or out — preserves chain context).
    # Also filter out anything any Result includes.
    deletable: list[dict] = []
    kept_revision = 0
    kept_referenced = 0
    with _conn() as c:
        for r in candidates:
            eid = r["id"]
            # Has any wasRevisionOf edge (either direction)? Keep.
            rev = c.execute(
                "SELECT 1 FROM entity_edges WHERE rel_type='wasRevisionOf' "
                "AND (source_id=? OR target_id=?) LIMIT 1",
                (eid, eid),
            ).fetchone()
            if rev:
                kept_revision += 1
                continue
            # Has any incoming `includes` (a Result wraps it)? Keep.
            inc = c.execute(
                "SELECT 1 FROM entity_edges WHERE rel_type='includes' "
                "AND target_id=? LIMIT 1",
                (eid,),
            ).fetchone()
            if inc:
                kept_referenced += 1
                continue
            deletable.append({"id": eid, "type": r["type"], "title": r["title"],
                              "exec_id": r["exec_id"],
                              "parent": r["parent_entity_id"],
                              "created_at": r["created_at"]})

    # Group by parent Run for the summary
    by_run: dict[str, list[dict]] = {}
    for d in deletable:
        by_run.setdefault(d["parent"] or "(no-parent)", []).append(d)

    print(f"[shadow-cleanup] {len(candidates)} unpinned active figure/table rows examined")
    print(f"[shadow-cleanup]   keeping {kept_revision} with wasRevisionOf edges")
    print(f"[shadow-cleanup]   keeping {kept_referenced} referenced by Results")
    print(f"[shadow-cleanup]   {len(deletable)} candidates for deletion")
    print()
    for run_id, items in sorted(by_run.items(), key=lambda kv: -len(kv[1])):
        print(f"  parent={run_id}  ({len(items)} items)")
        for d in items[:5]:
            print(f"    - {d['type']:6}  {d['id']}  {d['title'][:60]!r}")
        if len(items) > 5:
            print(f"    … and {len(items) - 5} more")
    print()

    if not args.apply:
        print("[shadow-cleanup] DRY RUN. Re-run with --apply to delete.")
        return 0

    # Apply: hard-delete the rows + their outgoing edges. The graph entity
    # API has delete_entity_hard which handles this.
    from core.graph.entities import delete_entity_hard
    deleted = 0
    for d in deletable:
        try:
            ok = delete_entity_hard(d["id"])
            if ok:
                deleted += 1
        except Exception as e:  # noqa: BLE001
            print(f"[shadow-cleanup] failed to delete {d['id']}: {e}")
    print(f"[shadow-cleanup] deleted {deleted}/{len(deletable)} rows")

    # Refresh affected Run manifests so the Files tree + Run view reflect
    # the changes.
    refreshed = 0
    for run_id in by_run:
        if not run_id or run_id == "(no-parent)":
            continue
        try:
            from content.bio.lifecycle.runs import refresh_output_manifest
            refresh_output_manifest(run_id)
            refreshed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[shadow-cleanup] failed to refresh manifest for {run_id}: {e}")
    print(f"[shadow-cleanup] refreshed {refreshed} run manifest(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
