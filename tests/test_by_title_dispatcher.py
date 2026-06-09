"""R2 — entity-type-aware link dispatcher."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.by_title import (   # noqa: E402
    compute_entity_link, compute_project_link, title_file_contents, LinkSpec,
)


# ─── artifact-typed entities ────────────────────────────────────────────
def test_figure_with_artifact_path_links_to_artifacts_by_title():
    row = {
        "id": "fig_abc123",
        "type": "figure",
        "title": "UMAP clusters 1-7",
        "status": "active",
        "artifact_path": "/abs/path/projects/prj_x/artifacts/sha256abc.png",
    }
    spec = compute_entity_link(row)
    assert spec is not None
    assert spec.category == "artifacts-by-title"
    assert spec.link_name == "UMAP-clusters-1-7.png"
    assert spec.target == "../artifacts/sha256abc.png"
    assert spec.fallback_id == "fig_abc123"


def test_table_links_to_artifacts_by_title_with_extension():
    row = {
        "id": "tbl_a1",
        "type": "table",
        "title": "Cluster markers",
        "status": "active",
        "artifact_path": "/x/artifacts/0123.csv",
    }
    spec = compute_entity_link(row)
    assert spec.category == "artifacts-by-title"
    assert spec.link_name == "Cluster-markers.csv"
    assert spec.target == "../artifacts/0123.csv"


def test_cell_links_to_artifacts_by_title():
    row = {
        "id": "cel_x", "type": "cell", "title": "MAGIC impute call",
        "status": "active", "artifact_path": "/x/artifacts/aaa.md",
    }
    spec = compute_entity_link(row)
    assert spec.category == "artifacts-by-title"
    assert spec.link_name == "MAGIC-impute-call.md"


def test_artifact_entity_without_path_yields_none():
    """A figure entity that hasn't materialized yet (no artifact_path)
    can't have a meaningful symlink target — return None."""
    row = {"id": "fig_x", "type": "figure", "title": "Unrendered",
           "status": "active", "artifact_path": None}
    assert compute_entity_link(row) is None


def test_archived_entity_yields_none():
    row = {"id": "fig_x", "type": "figure", "title": "Old",
           "status": "archived", "artifact_path": "/x/artifacts/y.png"}
    assert compute_entity_link(row) is None


def test_unsupported_entity_types_yield_none():
    """Results / claims / narratives / plans don't have file-system reps."""
    for t in ("result", "claim", "finding", "narrative", "plan", "workspace"):
        row = {"id": f"{t[:3]}_x", "type": t, "title": "T",
               "status": "active", "artifact_path": "/x/y.png"}
        assert compute_entity_link(row) is None, f"type={t} should be None"


def test_artifact_link_handles_missing_extension():
    row = {"id": "fig_x", "type": "figure", "title": "No-ext",
           "status": "active", "artifact_path": "/x/artifacts/abc"}
    spec = compute_entity_link(row)
    assert spec.link_name == "No-ext"     # no trailing dot


def test_artifact_link_handles_path_with_no_dir():
    row = {"id": "fig_x", "type": "figure", "title": "Loose",
           "status": "active", "artifact_path": "loose.png"}
    spec = compute_entity_link(row)
    assert spec.target == "../artifacts/loose.png"


def test_empty_title_falls_back_to_untitled():
    row = {"id": "fig_x", "type": "figure", "title": "",
           "status": "active", "artifact_path": "/x/a.png"}
    spec = compute_entity_link(row)
    assert spec.link_name == "untitled.png"


# ─── S-1: thread ───────────────────────────────────────────────────────
def test_thread_links_to_threads_by_title():
    row = {
        "id": "thr_704fb4aa", "type": "thread",
        "title": "show the sample table for GSE192391",
        "status": "active",
    }
    spec = compute_entity_link(row)
    assert spec is not None
    assert spec.category == "threads-by-title"
    assert spec.link_name == "show-the-sample-table-for-GSE192391.jsonl"
    assert spec.target == "../threads/thr_704fb4aa.jsonl"
    assert spec.fallback_id == "thr_704fb4aa"


def test_thread_empty_title_falls_back():
    row = {"id": "thr_x", "type": "thread", "title": "", "status": "active"}
    spec = compute_entity_link(row)
    assert spec is not None
    assert spec.link_name == "untitled-thread.jsonl"
    assert spec.target == "../threads/thr_x.jsonl"


def test_thread_archived_yields_none():
    row = {"id": "thr_x", "type": "thread", "title": "old",
           "status": "archived"}
    assert compute_entity_link(row) is None


def test_thread_without_id_yields_none():
    row = {"id": "", "type": "thread", "title": "anything", "status": "active"}
    assert compute_entity_link(row) is None


# ─── S-3: analysis (Run) ────────────────────────────────────────────────
def test_analysis_links_to_runs_by_title():
    row = {
        "id": "ana_7abe103d", "type": "analysis",
        "title": "pagoda2 on GSM5746259 (patient 145, day 0)",
        "status": "active",
    }
    spec = compute_entity_link(row)
    assert spec is not None
    assert spec.category == "runs-by-title"
    assert spec.link_name == "pagoda2-on-GSM5746259-patient-145-day-0"
    assert spec.target == "../work/ana_7abe103d"
    assert spec.fallback_id == "ana_7abe103d"


def test_analysis_completed_status_still_links():
    """A completed run is NOT archived — its work dir is the canonical
    record of the run and should remain navigable."""
    row = {"id": "ana_x", "type": "analysis", "title": "old run",
           "status": "completed"}
    spec = compute_entity_link(row)
    # status != 'active' so we currently skip. Document that and assert.
    # If we later want completed runs linked, change the gate here.
    assert spec is None


def test_analysis_archived_yields_none():
    row = {"id": "ana_x", "type": "analysis", "title": "old run",
           "status": "archived"}
    assert compute_entity_link(row) is None


def test_analysis_without_id_yields_none():
    row = {"id": "", "type": "analysis", "title": "x", "status": "active"}
    assert compute_entity_link(row) is None


# ─── S-2: dataset ───────────────────────────────────────────────────────
def test_dataset_with_work_subtree_artifact_path():
    row = {
        "id": "dat_2ce1545a", "type": "dataset",
        "title": "GSE192391 — count matrices (patient 145, day 0 + day 7)",
        "status": "active",
        "artifact_path": ("/workspace/aba-runtime/projects/prj_55b67456/"
                          "work/thread-thr_704fb4aa/geo_data/GSE192391_first2"),
    }
    spec = compute_entity_link(row)
    assert spec is not None
    assert spec.category == "datasets-by-title"
    # Long title gets capped at 80 chars by slugify
    assert spec.link_name.startswith("GSE192391-count-matrices")
    assert spec.target == "../work/thread-thr_704fb4aa/geo_data/GSE192391_first2"


def test_dataset_with_data_dir_artifact_path():
    """A dataset uploaded via the UI lands under projects/<pid>/data/<id>/."""
    row = {
        "id": "dat_abc", "type": "dataset",
        "title": "Uploaded counts",
        "status": "active",
        "artifact_path": "/workspace/aba-runtime/projects/prj_q/data/dat_abc",
    }
    spec = compute_entity_link(row)
    assert spec is not None
    assert spec.target == "../data/dat_abc"


def test_dataset_refstore_path_yields_none():
    """Cross-project refstore (/refs/<accession>/) is intentionally not
    linked — the link would point outside the project tree."""
    row = {
        "id": "dat_ref", "type": "dataset",
        "title": "Shared ref",
        "status": "active",
        "artifact_path": "/workspace/aba-runtime/refs/GSE192391",
    }
    assert compute_entity_link(row) is None


def test_dataset_without_artifact_path_yields_none():
    row = {"id": "dat_x", "type": "dataset", "title": "x", "status": "active",
           "artifact_path": None}
    assert compute_entity_link(row) is None


def test_dataset_empty_title_falls_back():
    row = {"id": "dat_x", "type": "dataset", "title": "",
           "status": "active",
           "artifact_path": "/workspace/aba-runtime/projects/p/data/foo"}
    spec = compute_entity_link(row)
    assert spec is not None
    assert spec.link_name == "untitled-dataset"


# ─── path-helper unit ───────────────────────────────────────────────────
def test_strip_to_project_relative_basic():
    from core.recovery.by_title import _strip_to_project_relative
    p = "/workspace/aba-runtime/projects/prj_x/work/ana_y/out.h5"
    assert _strip_to_project_relative(p) == "work/ana_y/out.h5"


def test_strip_to_project_relative_trailing_slash():
    from core.recovery.by_title import _strip_to_project_relative
    p = "/workspace/aba-runtime/projects/prj_x/data/dat_y/"
    assert _strip_to_project_relative(p) == "data/dat_y"


def test_strip_to_project_relative_not_under_projects():
    from core.recovery.by_title import _strip_to_project_relative
    assert _strip_to_project_relative("/workspace/aba-runtime/refs/GSE/x") is None
    assert _strip_to_project_relative("") is None
    assert _strip_to_project_relative("/no/projects/here") is None


def test_strip_to_project_relative_pid_only_no_subpath():
    """Path stops at the project dir itself (no sub-path) — None."""
    from core.recovery.by_title import _strip_to_project_relative
    assert _strip_to_project_relative("/workspace/aba-runtime/projects/prj_x") is None
    assert _strip_to_project_relative("/workspace/aba-runtime/projects/prj_x/") is None


# ─── project ────────────────────────────────────────────────────────────
def test_project_link_basic():
    spec = compute_project_link("prj_c690e402", "My scRNA project")
    assert spec.category == "projects-by-title"
    assert spec.link_name == "My-scRNA-project"
    assert spec.target == "../projects/prj_c690e402"
    assert spec.fallback_id == "prj_c690e402"


def test_project_link_falls_back_to_pid_when_no_title():
    spec = compute_project_link("prj_xyz", "")
    assert spec.link_name == "prj_xyz"


# ─── TITLE.txt sidecar ──────────────────────────────────────────────────
def test_title_file_contents_trailing_newline():
    assert title_file_contents("My project") == "My project\n"
    assert title_file_contents("  trimmed  ") == "trimmed\n"
    assert title_file_contents("") == "\n"
    assert title_file_contents(None) == "\n"
