"""aba-recover CLI: rebuild project DB(s) from on-disk recovery archives.

Subcommands:
  recover   <project-dir>            Rebuild project.db from sidecars + logs.
  backfill  <project-dir>            Rewrite sidecars + logs from the live DB.
  verify    <project-dir>            Dry-run recovery into a temp DB + report.

Bulk mode (I4):
  --all-under <runtime/projects>     Iterate every <pid>/ subdir.

Run: .venv/bin/python -m core.recovery.cli <subcommand> ...
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from core.recovery.walker import recover_project, backfill_project


def _emit_report(report) -> None:
    """Pretty-print a RecoverReport to stdout."""
    d = report.to_dict() if hasattr(report, "to_dict") else report
    print(json.dumps(d, indent=2))


def _cmd_recover(args) -> int:
    src = Path(args.path)
    if not src.is_dir():
        print(f"error: not a directory: {src}", file=sys.stderr)
        return 2
    report = recover_project(src, target_db=Path(args.into) if args.into else None,
                             dry_run=False)
    _emit_report(report)
    return 0


def _cmd_backfill(args) -> int:
    src = Path(args.path)
    if not src.is_dir():
        print(f"error: not a directory: {src}", file=sys.stderr)
        return 2
    report = backfill_project(src)
    _emit_report(report)
    return 0


def _cmd_verify(args) -> int:
    src = Path(args.path)
    if not src.is_dir():
        print(f"error: not a directory: {src}", file=sys.stderr)
        return 2
    report = recover_project(src, dry_run=True)
    _emit_report(report)
    return 0


def _cmd_all_under(args) -> int:
    """Bulk recovery: walk every subdir of <root> and recover each."""
    root = Path(args.root)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    reports = []
    failed = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name.startswith("_") or sub.name.startswith("."):
            continue   # _workspace, _scratch, hidden — skip
        if not (sub / "project.json").exists():
            continue   # not a recovery-shaped dir
        try:
            r = recover_project(sub, dry_run=args.dry_run)
            reports.append(r.to_dict())
        except Exception as e:
            failed.append({"path": str(sub), "error": str(e)})
    print(json.dumps({"recovered": reports, "failed": failed}, indent=2))
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aba-recover", description="Project recovery CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("recover", help="Rebuild project.db from sidecars + logs")
    pr.add_argument("path", help="path to projects/<pid>/")
    pr.add_argument("--into", default=None, help="target DB file (default: <path>/project.db)")
    pr.set_defaults(fn=_cmd_recover)

    pb = sub.add_parser("backfill", help="Rewrite sidecars/logs from the live DB")
    pb.add_argument("path", help="path to projects/<pid>/")
    pb.set_defaults(fn=_cmd_backfill)

    pv = sub.add_parser("verify", help="Dry-run recovery for drift check")
    pv.add_argument("path", help="path to projects/<pid>/")
    pv.set_defaults(fn=_cmd_verify)

    pa = sub.add_parser("all-under", help="Bulk: recover every <pid>/ subdir")
    pa.add_argument("root", help="path to runtime/projects/")
    pa.add_argument("--dry-run", action="store_true")
    pa.set_defaults(fn=_cmd_all_under)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
