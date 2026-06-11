"""PROJECT-snapshot sidebar surfaces the dataset's layout_hint and the
'use list_data_files' nudge AT THE BLOCK (not buried 10K characters
later in the Paths paragraph).

Live friction (prj_ab1b55fe thr_e692a202, 2026-06-11): even on Opus,
the agent went through three rounds of relative-path guessing in one
session — Seurat, scanpy, and integration — because the PROJECT block
showed only the dataset directory path. The agent invented filenames
like 'GSM5746259_matrix.mtx.gz' when the real names had a sample-
metadata suffix.

register_dataset already writes metadata.layout_hint (curation.py:347);
sidebar.py just wasn't reading it.

Run: .venv/bin/python tests/test_sidebar_dataset_hint.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_sidebar_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "sb.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]    = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db    # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402
from core.graph.entities import create_entity                   # noqa: E402
from content.bio.cards.sidebar import render_bio_project_sidebar  # noqa: E402


_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    # ── 1. layout_hint shows up on the dataset line ─────────────────
    print("dataset with layout_hint metadata is surfaced in the snapshot")
    p = Path(_tmp) / "data" / "geo_data"
    p.mkdir(parents=True, exist_ok=True)
    _ = create_entity(
        entity_type="dataset",
        title="GSE192391 – first two COVID-19 samples",
        artifact_path=str(p),
        metadata={"thread_id": "t",
                  "layout_hint": "6 flat files (.mtx.gz, .tsv.gz)"},
    )
    text = render_bio_project_sidebar(thread_id="t")
    check("snapshot non-empty", bool(text), text[:200])
    check("dataset title rendered",
          "GSE192391 – first two COVID-19 samples" in text)
    check("artifact_path rendered",
          str(p) in text, text)
    check("layout_hint rendered next to the path",
          "6 flat files (.mtx.gz, .tsv.gz)" in text,
          text)
    # The nudge is on the same block so the agent reads it together
    # with the path. We don't pin exact phrasing — just keywords.
    check("list_data_files nudge present at the block",
          "list_data_files" in text, text)
    check("nudge mentions cwd / relative caveat",
          "cwd" in text or "relative" in text, text)

    # ── 2. dataset WITHOUT layout_hint still renders cleanly ─────────
    print("\ndataset with NO layout_hint just shows path (no '·' dangling)")
    p2 = Path(_tmp) / "data" / "other"
    p2.mkdir(parents=True, exist_ok=True)
    _ = create_entity(
        entity_type="dataset", title="Plain dataset",
        artifact_path=str(p2),
        metadata={"thread_id": "t"},   # no layout_hint
    )
    text2 = render_bio_project_sidebar(thread_id="t")
    # Find the "Plain dataset" line specifically and confirm it doesn't
    # end with " · " (the separator we use before the layout hint).
    plain_line = next(l for l in text2.splitlines()
                      if "Plain dataset" in l)
    check("plain dataset line has the path",
          str(p2) in plain_line, plain_line)
    check("plain dataset line has NO dangling separator",
          " ·  " not in plain_line and not plain_line.rstrip().endswith("·"),
          plain_line)

    # ── 3. Empty project → empty string (regression guard) ─────────
    print("\nempty project still suppresses the snapshot block")
    # Fresh DB to simulate empty state.
    _tmp2 = tempfile.mkdtemp(prefix="aba_sidebar_empty_")
    db2 = str(Path(_tmp2) / "empty.db")
    set_db_path(db2)
    init_db()
    check("no datasets → no snapshot",
          render_bio_project_sidebar() == "")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL SIDEBAR CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
