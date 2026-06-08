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
    for t in ("result", "claim", "finding", "narrative", "plan",
              "workspace", "thread", "analysis", "dataset"):
        row = {"id": f"{t[:3]}_x", "type": t, "title": "T",
               "status": "active", "artifact_path": "/x/y.png"}
        # type IN unsupported set → None (defer non-artifact types to a
        # future R3 expansion that knows about work_dir, data_dir, …)
        assert compute_entity_link(row) is None, f"type={t} should be None for v1"


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
