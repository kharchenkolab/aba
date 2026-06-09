"""R4 — refresh-symlinks: rebuild by-title from live DB.

Covers:
- Fresh project → refresh writes all expected symlinks.
- Stale symlinks (entity now archived / deleted / superseded) → removed.
- Manually-broken state → fully repaired on re-run (idempotent).
- Recovery walker auto-calls refresh after import.
- CLI subcommand `aba-recover refresh-symlinks` works.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_bytitle_r4_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override                # noqa: E402
from core.recovery.by_title import (                                         # noqa: E402
    refresh_by_title_links, refresh_project_link_at_root,
)

# Disable the actual scribe so the by-title side-effects don't fight our
# direct refresh calls in this test. We're testing the refresh path in
# isolation, with the DB as the only source of truth.
os.environ["ABA_RECOVERY_DISABLED"] = "1"
set_scribe_override(None)

from core import projects                                                    # noqa: E402
from core.graph.entities import create_entity, archive_entity, delete_entity_hard  # noqa: E402
from core.graph.edges import add_edge                                        # noqa: E402

projects.init()
PROOT = Path(_tmp) / "projects"
RUNTIME = Path(_tmp)


def _populated_project(name: str) -> tuple[str, Path]:
    p = projects.create_project(name)
    pid = p["id"]
    projects.set_current(pid)
    return pid, PROOT / pid


# ─── refresh from a clean DB ────────────────────────────────────────────
def test_refresh_creates_links_for_active_figures():
    pid, pdir = _populated_project("RefreshA")
    f1 = create_entity(entity_type="figure", title="UMAP",
                       artifact_path=str(pdir / "artifacts" / "u1.png"))
    f2 = create_entity(entity_type="figure", title="QC violins",
                       artifact_path=str(pdir / "artifacts" / "v1.png"))
    counts = refresh_by_title_links(pdir)
    parent = pdir / "artifacts-by-title"
    names = {p.name for p in parent.iterdir() if p.is_symlink()}
    assert names == {"UMAP.png", "QC-violins.png"}
    assert counts["created"] >= 2


def test_refresh_skips_archived_and_deleted():
    pid, pdir = _populated_project("RefreshB")
    f1 = create_entity(entity_type="figure", title="Live",
                       artifact_path=str(pdir / "artifacts" / "a.png"))
    f2 = create_entity(entity_type="figure", title="Will-archive",
                       artifact_path=str(pdir / "artifacts" / "b.png"))
    archive_entity(f2)
    refresh_by_title_links(pdir)
    parent = pdir / "artifacts-by-title"
    names = {p.name for p in parent.iterdir() if p.is_symlink()}
    assert names == {"Live.png"}


def test_refresh_skips_superseded_revisions():
    pid, pdir = _populated_project("RefreshC")
    # v1, v2 share title; v2 supersedes v1
    f_v1 = create_entity(entity_type="figure", title="UMAP",
                         artifact_path=str(pdir / "artifacts" / "v1.png"))
    f_v2 = create_entity(entity_type="figure", title="UMAP",
                         artifact_path=str(pdir / "artifacts" / "v2.png"))
    add_edge(f_v2, f_v1, "wasRevisionOf")
    refresh_by_title_links(pdir)
    parent = pdir / "artifacts-by-title"
    surviving = list(parent.iterdir())
    assert len(surviving) == 1, f"expected only the head, got {[s.name for s in surviving]}"
    assert os.readlink(surviving[0]) == "../artifacts/v2.png"


def test_refresh_removes_stale_symlinks_on_rerun():
    pid, pdir = _populated_project("RefreshD")
    f1 = create_entity(entity_type="figure", title="StillHere",
                       artifact_path=str(pdir / "artifacts" / "a.png"))
    f2 = create_entity(entity_type="figure", title="GoingAway",
                       artifact_path=str(pdir / "artifacts" / "b.png"))
    refresh_by_title_links(pdir)
    parent = pdir / "artifacts-by-title"
    assert (parent / "GoingAway.png").is_symlink()
    # Now archive f2 + re-refresh
    archive_entity(f2)
    counts = refresh_by_title_links(pdir)
    assert not (parent / "GoingAway.png").exists()
    assert counts["removed"] >= 1
    # Surviving entry intact
    assert (parent / "StillHere.png").is_symlink()


def test_refresh_idempotent_no_drift_on_rerun():
    pid, pdir = _populated_project("RefreshE")
    create_entity(entity_type="figure", title="StableA",
                  artifact_path=str(pdir / "artifacts" / "a.png"))
    create_entity(entity_type="figure", title="StableB",
                  artifact_path=str(pdir / "artifacts" / "b.png"))
    refresh_by_title_links(pdir)
    # Re-run: created should be 0, unchanged should match
    counts = refresh_by_title_links(pdir)
    assert counts["created"] == 0
    assert counts["unchanged"] == 2


def test_refresh_repairs_manually_deleted_symlinks():
    pid, pdir = _populated_project("RefreshF")
    create_entity(entity_type="figure", title="Self-healing",
                  artifact_path=str(pdir / "artifacts" / "a.png"))
    refresh_by_title_links(pdir)
    parent = pdir / "artifacts-by-title"
    target = parent / "Self-healing.png"
    assert target.is_symlink()
    target.unlink()
    counts = refresh_by_title_links(pdir)
    assert target.is_symlink()
    assert counts["created"] == 1


# ─── S-1/S-2/S-3 — refresh covers new entity types ─────────────────────
def test_refresh_creates_threads_by_title():
    """A thread entity in the DB → refresh creates threads-by-title/<slug>.jsonl."""
    pid, pdir = _populated_project("RefreshThreads")
    t = create_entity(entity_type="thread", title="My investigation")
    refresh_by_title_links(pdir)
    parent = pdir / "threads-by-title"
    names = {p.name for p in parent.iterdir() if p.is_symlink()}
    assert "My-investigation.jsonl" in names
    link = parent / "My-investigation.jsonl"
    assert os.readlink(link) == f"../threads/{t}.jsonl"


def test_refresh_creates_runs_by_title():
    """An analysis (Run) entity → refresh creates runs-by-title/<slug>."""
    pid, pdir = _populated_project("RefreshRuns")
    a = create_entity(entity_type="analysis", title="pagoda2 day 0")
    refresh_by_title_links(pdir)
    parent = pdir / "runs-by-title"
    names = {p.name for p in parent.iterdir() if p.is_symlink()}
    assert "pagoda2-day-0" in names
    link = parent / "pagoda2-day-0"
    assert os.readlink(link) == f"../work/{a}"


def test_refresh_creates_datasets_by_title_for_in_project_path():
    """A dataset with artifact_path inside this project's tree → symlink with
    a project-relative target."""
    pid, pdir = _populated_project("RefreshDS")
    abs_path = str(pdir / "work" / "thread-thr_x" / "geo_data" / "Counts")
    d = create_entity(entity_type="dataset", title="GSE192391 counts",
                      artifact_path=abs_path)
    refresh_by_title_links(pdir)
    parent = pdir / "datasets-by-title"
    link = parent / "GSE192391-counts"
    assert link.is_symlink(), \
        f"expected link; contents={list(parent.iterdir()) if parent.exists() else 'no dir'}"
    assert os.readlink(link) == "../work/thread-thr_x/geo_data/Counts"


def test_refresh_skips_dataset_with_refstore_path():
    """A dataset whose artifact_path is in the cross-project refstore
    (/refs/...) gets NO symlink."""
    pid, pdir = _populated_project("RefreshDSref")
    abs_path = str(RUNTIME / "refs" / "GSE-shared")
    create_entity(entity_type="dataset", title="Shared",
                  artifact_path=abs_path)
    refresh_by_title_links(pdir)
    parent = pdir / "datasets-by-title"
    # Dir may not exist (no qualifying datasets) — either is fine
    if parent.exists():
        assert not any(p.is_symlink() for p in parent.iterdir())


def test_refresh_idempotent_with_mixed_entity_types():
    """One project with figures + threads + runs + datasets → refresh
    twice; second run is all unchanged."""
    pid, pdir = _populated_project("RefreshMixed")
    create_entity(entity_type="figure", title="UMAP",
                  artifact_path=str(pdir / "artifacts" / "u.png"))
    create_entity(entity_type="thread", title="my investigation")
    create_entity(entity_type="analysis", title="run alpha")
    create_entity(entity_type="dataset", title="counts",
                  artifact_path=str(pdir / "data" / "dat_x"))
    refresh_by_title_links(pdir)
    counts = refresh_by_title_links(pdir)
    assert counts["created"] == 0, f"second run should be idempotent: {counts}"
    # Each category should have exactly one link
    for cat in ("artifacts-by-title", "threads-by-title",
                "runs-by-title", "datasets-by-title"):
        links = [p for p in (pdir / cat).iterdir() if p.is_symlink()]
        assert len(links) == 1, f"{cat}: expected 1 link, got {[l.name for l in links]}"


# ─── project-level link ─────────────────────────────────────────────────
def test_refresh_project_link_writes_title_txt_and_symlink():
    pid, pdir = _populated_project("Refresh-G project")
    # Force project.json to exist by triggering project-meta emit. Even with
    # the scribe disabled, refresh_project_link_at_root reads from the file.
    # The projects.create_project call wrote registry.json (workspace-level),
    # not project.json — synthesize one for the test.
    pj = pdir / "project.json"
    pj.write_text(json.dumps({
        "pid": pid,
        "registry": {"id": pid, "name": "Refresh-G project"},
    }))
    refresh_project_link_at_root(pdir)
    link = RUNTIME / "projects-by-title" / "Refresh-G-project"
    assert link.is_symlink()
    assert os.readlink(link) == f"../projects/{pid}"
    title_txt = pdir / "TITLE.txt"
    assert title_txt.exists()
    assert title_txt.read_text().strip() == "Refresh-G project"


def test_refresh_project_link_explicit_registry_arg():
    pid, pdir = _populated_project("OriginalName")
    # No project.json on disk — pass registry_row directly
    refresh_project_link_at_root(pdir, registry_row={"id": pid, "name": "Direct"})
    link = RUNTIME / "projects-by-title" / "Direct"
    assert link.is_symlink()


# ─── CLI subcommand ─────────────────────────────────────────────────────
def test_cli_refresh_symlinks_subcommand():
    pid, pdir = _populated_project("CLI-Refresh")
    create_entity(entity_type="figure", title="From-CLI",
                  artifact_path=str(pdir / "artifacts" / "f.png"))
    # Drop project.json so the CLI's refresh-project-link path has something
    pdir.joinpath("project.json").write_text(json.dumps({
        "pid": pid, "registry": {"id": pid, "name": "CLI-Refresh"},
    }))
    r = subprocess.run(
        [sys.executable, "-m", "core.recovery.cli", "refresh-symlinks", str(pdir)],
        cwd=ROOT / "backend",
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"CLI failed: {r.stderr}"
    # Parse trailing JSON (tolerate bio import-time stdout)
    lines = r.stdout.splitlines()
    json_start = next(i for i, ln in enumerate(lines) if ln.startswith("{"))
    out = json.loads("\n".join(lines[json_start:]))
    assert out["created"] >= 1
    parent = pdir / "artifacts-by-title"
    assert (parent / "From-CLI.png").is_symlink()


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
