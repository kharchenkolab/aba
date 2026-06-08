"""R3 — scribe wiring for by-title symlinks.

Drives the real Scribe (with the new R3 side-effects) and asserts the
expected directory contents under the project root + the runtime root.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_bytitle_scribe_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import (   # noqa: E402
    Scribe, set_scribe_override,
    EntityUpserted, EntityHardDeleted, EdgeOp, ProjectMetaChanged,
)

# Resolve PROOT dynamically via core.config rather than the module-local _tmp
# constant — pytest may collect multiple test files in one process and the
# first file's import of core.config locks in its PROJECTS_DIR. We want our
# assertions to look where the scribe actually wrote.
from core import config as _config   # noqa: E402

def _PROOT() -> Path:
    return _config.PROJECTS_DIR

def _RUNTIME() -> Path:
    return _config.PROJECTS_DIR.parent


def _fresh_scribe():
    s = Scribe(tick_interval=10_000.0)
    set_scribe_override(s)
    return s


# ─── basic figure linking ───────────────────────────────────────────────
def test_figure_upsert_creates_symlink():
    s = _fresh_scribe()
    s.enqueue(EntityUpserted(pid="prj_a", entity_id="fig_a1", row={
        "id": "fig_a1", "type": "figure",
        "title": "UMAP clusters 1-7",
        "status": "active",
        "artifact_path": str(_PROOT() / "prj_a" / "artifacts" / "abc.png"),
    }))
    s.flush()
    link = _PROOT() / "prj_a" / "artifacts-by-title" / "UMAP-clusters-1-7.png"
    assert link.is_symlink()
    assert os.readlink(link) == "../artifacts/abc.png"


def test_table_upsert_creates_symlink_with_csv():
    s = _fresh_scribe()
    s.enqueue(EntityUpserted(pid="prj_t", entity_id="tbl_a", row={
        "id": "tbl_a", "type": "table", "title": "Marker genes",
        "status": "active", "artifact_path": "/some/where/data.csv",
    }))
    s.flush()
    link = _PROOT() / "prj_t" / "artifacts-by-title" / "Marker-genes.csv"
    assert link.is_symlink()
    assert os.readlink(link) == "../artifacts/data.csv"


# ─── title rename ───────────────────────────────────────────────────────
def test_title_change_unlinks_old_and_links_new():
    s = _fresh_scribe()
    pid = "prj_b"
    s.enqueue(EntityUpserted(pid=pid, entity_id="fig_b1", row={
        "id": "fig_b1", "type": "figure", "title": "First name",
        "status": "active", "artifact_path": "/x/abc.png",
    }))
    s.flush()
    parent = _PROOT() / pid / "artifacts-by-title"
    assert (parent / "First-name.png").is_symlink()

    # Rename — same entity, different title
    s.enqueue(EntityUpserted(pid=pid, entity_id="fig_b1", row={
        "id": "fig_b1", "type": "figure", "title": "Second name",
        "status": "active", "artifact_path": "/x/abc.png",
    }))
    s.flush()
    assert (parent / "Second-name.png").is_symlink()
    # Old slug removed
    assert not (parent / "First-name.png").exists()


def test_no_op_when_title_unchanged():
    """Calling EntityUpserted with same title twice should not double-write
    the symlink (cache hit). We can't easily observe 'no-op' from outside,
    but the post-state is identical and no extra entries appear."""
    s = _fresh_scribe()
    payload = {
        "id": "fig_c1", "type": "figure", "title": "Stable",
        "status": "active", "artifact_path": "/x/y.png",
    }
    s.enqueue(EntityUpserted(pid="prj_c", entity_id="fig_c1", row=payload))
    s.flush()
    s.enqueue(EntityUpserted(pid="prj_c", entity_id="fig_c1", row=payload))
    s.flush()
    parent = _PROOT() / "prj_c" / "artifacts-by-title"
    assert {p.name for p in parent.iterdir() if p.is_symlink()} == {"Stable.png"}


# ─── archived + hard-deleted ────────────────────────────────────────────
def test_archive_clears_symlink():
    s = _fresh_scribe()
    pid = "prj_d"
    s.enqueue(EntityUpserted(pid=pid, entity_id="fig_d1", row={
        "id": "fig_d1", "type": "figure", "title": "Goes away",
        "status": "active", "artifact_path": "/x/y.png",
    }))
    s.flush()
    parent = _PROOT() / pid / "artifacts-by-title"
    assert (parent / "Goes-away.png").is_symlink()

    # Archive — same entity, status now 'archived' → compute_entity_link returns None
    s.enqueue(EntityUpserted(pid=pid, entity_id="fig_d1", row={
        "id": "fig_d1", "type": "figure", "title": "Goes away",
        "status": "archived", "artifact_path": "/x/y.png",
    }))
    s.flush()
    assert not (parent / "Goes-away.png").exists()


def test_hard_delete_clears_symlink():
    s = _fresh_scribe()
    pid = "prj_e"
    s.enqueue(EntityUpserted(pid=pid, entity_id="fig_e1", row={
        "id": "fig_e1", "type": "figure", "title": "Doomed",
        "status": "active", "artifact_path": "/x/y.png",
    }))
    s.flush()
    parent = _PROOT() / pid / "artifacts-by-title"
    assert (parent / "Doomed.png").is_symlink()
    s.enqueue(EntityHardDeleted(pid=pid, entity_id="fig_e1"))
    s.flush()
    assert not (parent / "Doomed.png").exists()


# ─── revision supersession ──────────────────────────────────────────────
def test_was_revision_of_clears_predecessor_link():
    """When fig_v2 --wasRevisionOf--> fig_v1, fig_v1 is the old version and
    its by-title link should be cleared so only the head (fig_v2) shows up."""
    s = _fresh_scribe()
    pid = "prj_f"
    # v1 and v2 both have the same title; expect collision-suffixing then
    # supersession to clear v1's link.
    s.enqueue(EntityUpserted(pid=pid, entity_id="fig_v1", row={
        "id": "fig_v1", "type": "figure", "title": "UMAP",
        "status": "active", "artifact_path": "/x/v1.png",
    }))
    s.flush()
    parent = _PROOT() / pid / "artifacts-by-title"
    assert (parent / "UMAP.png").is_symlink()
    assert os.readlink(parent / "UMAP.png") == "../artifacts/v1.png"

    # v2 (new head) — same title, different artifact; will collision-suffix
    s.enqueue(EntityUpserted(pid=pid, entity_id="fig_v2", row={
        "id": "fig_v2", "type": "figure", "title": "UMAP",
        "status": "active", "artifact_path": "/x/v2.png",
    }))
    s.flush()
    # Both v1 and v2 are now linked (with suffix on one)
    links = {p.name for p in parent.iterdir() if p.is_symlink()}
    assert "UMAP.png" in links and any("UMAP_" in n for n in links), \
        f"expected base + suffixed slug, got {links}"

    # Now record the supersession: v2 --wasRevisionOf--> v1
    s.enqueue(EdgeOp(pid=pid, op="add", src="fig_v2", dst="fig_v1", rel="wasRevisionOf"))
    s.flush()

    # v1's link should be gone; v2's stays
    surviving = {p.name for p in parent.iterdir() if p.is_symlink()}
    # Whatever v2's name is, v1's must be gone
    targets = {os.readlink(parent / n): n for n in surviving}
    assert "../artifacts/v1.png" not in targets, \
        f"superseded v1 link still present; surviving={surviving}, targets={targets}"
    assert "../artifacts/v2.png" in targets


# ─── project-level link + TITLE.txt ─────────────────────────────────────
def test_project_meta_writes_title_txt_and_projects_by_title_symlink():
    s = _fresh_scribe()
    pid = "prj_g"
    s.enqueue(ProjectMetaChanged(pid=pid, payload={
        "registry": {"id": pid, "name": "My scRNA project",
                     "created_at": "t", "last_touched": "t"},
        "project_entity": None,
    }))
    s.flush()

    title_file = _PROOT() / pid / "TITLE.txt"
    assert title_file.exists()
    assert title_file.read_text().strip() == "My scRNA project"

    link = _RUNTIME() / "projects-by-title" / "My-scRNA-project"
    assert link.is_symlink()
    assert os.readlink(link) == f"../projects/{pid}"


def test_project_rename_updates_symlink_and_title_txt():
    s = _fresh_scribe()
    pid = "prj_h"
    s.enqueue(ProjectMetaChanged(pid=pid, payload={
        "registry": {"id": pid, "name": "Original"},
    }))
    s.flush()
    parent = _RUNTIME() / "projects-by-title"
    assert (parent / "Original").is_symlink()

    s.enqueue(ProjectMetaChanged(pid=pid, payload={
        "registry": {"id": pid, "name": "Renamed"},
    }))
    s.flush()
    assert (parent / "Renamed").is_symlink()
    assert not (parent / "Original").exists()
    # TITLE.txt reflects new name
    assert (_PROOT() / pid / "TITLE.txt").read_text().strip() == "Renamed"


# ─── unsupported entity types are no-ops ────────────────────────────────
def test_result_entity_no_symlink():
    s = _fresh_scribe()
    pid = "prj_i"
    s.enqueue(EntityUpserted(pid=pid, entity_id="res_a", row={
        "id": "res_a", "type": "result", "title": "Headline result",
        "status": "active",
    }))
    s.flush()
    # No artifacts-by-title dir created (or if it exists, no link)
    parent = _PROOT() / pid / "artifacts-by-title"
    if parent.exists():
        assert not any(p.is_symlink() for p in parent.iterdir())


# ─── runner ─────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
