"""The project-wide name→file door (project_locate) — armed guards.

The defect class: a derived, lossy replica (walked project dirs / harvested
store) presented as the canonical place to look, while the primary stores
(live sandboxes, run manifests) were unsearched — so a file the agent just
wrote could be actively served yet report "not found". Guards, per the
ARMED/PROVEN/WIDE convention:
  - producer-fed: a file in a LIVE sandbox (local jobdir; remote inventory)
    is found and labeled — fails on the old 4-root walk;
  - link-only manifest rows (over-cap, never copied) resolve by name;
  - bounds are CONFESSED on hits and misses alike;
  - an unreachable site yields UNKNOWN, never silent absence;
  - collisions come back as labeled candidates, never a silent winner.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
_tmp = tempfile.mkdtemp(prefix="aba_locate_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_tmp, "t.db"))

from content.bio import project_locate as pl  # noqa: E402

pytestmark = pytest.mark.bio


def _no_kernels(monkeypatch):
    class _C:
        def sync_call(self, name, *a):
            return {"kernels": []}
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _C())


def test_live_local_sandbox_file_is_found(monkeypatch, tmp_path):
    """Producer-fed: a file in a live LOCAL kernel jobdir — invisible to the
    old root-walk — is found and labeled with its tier."""
    jd = tmp_path / "ws" / "site-local" / "krn_1"
    jd.mkdir(parents=True)
    (jd / "big_result.csv").write_text("x" * 100)

    class _C:
        def sync_call(self, name, *a):
            return {"kernels": [{"kernel_id": "krn_1", "site": "local",
                                 "jobdir": "krn_1"}]}
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _C())
    monkeypatch.setattr("core.compute.adapter.weft_workspace",
                        lambda: tmp_path / "ws")
    monkeypatch.setattr(pl, "_manifest_tier", lambda *a, **k: None)
    out = pl.locate_project_files("big_result.csv")
    assert out["matches"], out
    hit = out["matches"][0]
    assert hit["tier"] == "live sandbox" and hit["locality"] == "local"
    assert out["searched"]["live_kernels"]["local"] == 1


def test_remote_inventory_file_found_without_transfer(monkeypatch):
    """A file that exists only in a REMOTE sandbox is found via the held
    inventory (metadata), labeled remote, with the fetch cost in `opens`."""
    class _C:
        def sync_call(self, name, *a):
            return {"kernels": [{"kernel_id": "krn_r", "site": "siteB",
                                 "jobdir": "jd"}]}
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _C())
    monkeypatch.setattr("content.bio.tools.run_exec._kernel_sandbox_inventory",
                        lambda kid: {"out/huge_matrix.h5": 1721550000.0})
    monkeypatch.setattr(pl, "_manifest_tier", lambda *a, **k: None)
    out = pl.locate_project_files("huge_matrix.h5")
    assert out["matches"], out
    hit = out["matches"][0]
    assert hit["locality"] == "remote" and hit["site"] == "siteB"
    assert "fetches from siteB" in hit["opens"]


def test_unreachable_site_is_unknown_not_absent(monkeypatch):
    class _C:
        def sync_call(self, name, *a):
            return {"kernels": [{"kernel_id": "krn_x", "site": "siteX",
                                 "jobdir": "jd"}]}
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _C())

    def _boom(kid):
        raise ConnectionError("down")
    monkeypatch.setattr("content.bio.tools.run_exec._kernel_sandbox_inventory",
                        _boom)
    monkeypatch.setattr(pl, "_manifest_tier", lambda *a, **k: None)
    out = pl.locate_project_files("anything.csv")
    assert not out["matches"]
    assert any("UNKNOWN" in u for u in out.get("unsearched", [])), out
    assert "UNKNOWN" in out["note"] or "unsearched" in out["note"].lower()


def test_link_only_manifest_row_resolves_by_name(monkeypatch):
    """Over-cap output that never came home: the manifest's link-only row
    keeps the name real, with an honest opens line."""
    _no_kernels(monkeypatch)
    monkeypatch.setattr("core.graph.exec_records.list_recent_exec_ids",
                        lambda n: ["ex1"])
    monkeypatch.setattr("core.exec.artifacts.list_artifacts",
                        lambda ex: [{"original_name": "giant_table.parquet",
                                     "url": None, "size": 9 << 30,
                                     "kind": "file"}])
    out = pl.locate_project_files("giant_table.parquet")
    assert out["matches"], out
    hit = out["matches"][0]
    assert hit["tier"] == "run output" and hit["locality"] == "remote"
    assert "cap" in hit["opens"]
    assert out["searched"]["recent_execs"] == 1


def test_bounds_confessed_on_hit_and_miss(monkeypatch):
    _no_kernels(monkeypatch)
    monkeypatch.setattr("core.graph.exec_records.list_recent_exec_ids",
                        lambda n: [])
    out_miss = pl.locate_project_files("nope_*.bin")
    assert "searched" in out_miss and "note" in out_miss
    assert "recent executions" in out_miss["searched"]["note"]
    # and on a hit (drop a file into the searched data dir)
    from core.config import project_data_dir
    from core.projects import current_project_id
    pid = str(current_project_id() or "default")
    d = Path(str(project_data_dir(pid)))
    d.mkdir(parents=True, exist_ok=True)
    (d / "hit_bounds.csv").write_text("x")
    out_hit = pl.locate_project_files("hit_bounds.csv")
    assert out_hit["matches"] and "searched" in out_hit
    assert "recent executions" in out_hit["searched"]["note"]


def test_collision_returns_labeled_candidates(monkeypatch):
    """Same name from two runs → two candidates, each labeled by producer —
    never a silent single winner."""
    _no_kernels(monkeypatch)
    monkeypatch.setattr("core.graph.exec_records.list_recent_exec_ids",
                        lambda n: ["exA", "exB"])
    monkeypatch.setattr("core.exec.artifacts.list_artifacts",
                        lambda ex: [{"original_name": "metrics.csv",
                                     "url": None, "size": 10, "kind": "table"}])
    out = pl.locate_project_files("metrics.csv")
    assert len(out["matches"]) == 2
    assert {h["from_exec"] for h in out["matches"]} == {"exA", "exB"}


def test_no_private_tree_walks_outside_the_door():
    """The class-fix invariant: name-based resolvers must not grow their own
    tree-walks back. Every os.walk/rglob in the agent-tools layer is either
    the door itself or on the declared enumerator allowlist (listing surfaces,
    not name-resolvers). A new walker fails here with its location."""
    import re
    allowed = {
        "backend/content/bio/project_locate.py",       # THE door
        "backend/content/bio/tools/file_io.py",        # list_data_files enumerator
        "backend/content/bio/tools/curation.py",       # _describe_directory + hardlink copy
        "backend/content/bio/tools/run_exec.py",       # orientation banner enumerator
        "backend/content/bio/tools/plan_etc.py",       # run-output listing
        "backend/content/bio/lifecycle/runs.py",       # the RUN-scoped resolver (door delegates to it)
        "backend/content/bio/files/tree.py",           # file-tree listing endpoint (enumerator)
        "backend/content/bio/skills/__init__.py",      # skill discovery, not file lookup
        "backend/content/bio/web/routes/datasets.py",  # dataset listing route (enumerator)
        "backend/content/bio/mcp_servers/aba_core/tools/entity_ops.py",  # artifact-member listing
    }
    offenders = []
    for f in (ROOT / "backend/content/bio").rglob("*.py"):
        rel = str(f.relative_to(ROOT)).replace(os.sep, "/")
        if "mcp_servers" in rel and "tools/file_io" in rel:
            continue                        # registration shim only
        src = f.read_text()
        if re.search(r"\bos\.walk\(|\brglob\(", src) and rel not in allowed:
            offenders.append(rel)
    assert not offenders, (
        f"new private tree-walk(s) outside the door: {offenders} — route "
        f"name lookups through project_locate instead")


def test_read_path_ambiguity_lists_candidates(monkeypatch, tmp_path):
    """Two same-named local files → the read resolver refuses with LABELED
    candidates instead of silently picking one."""
    from content.bio.tools.file_io import _resolve_project_path
    monkeypatch.setattr(
        "content.bio.project_locate.locate_project_files",
        lambda pat, limit=6, ctx=None: {"matches": [
            {"path": "/a/m.csv", "tier": "run output", "from_exec": "exA"},
            {"path": "/b/m.csv", "tier": "user data"}]})
    _, err = _resolve_project_path("m.csv", {"thread_id": "t"}, must_exist=True,
                                   enforce_sandbox=False)
    assert err and "ambiguous" in err and "exA" in err


def test_read_path_remote_only_names_the_fate(monkeypatch):
    from content.bio.tools.file_io import _resolve_project_path
    monkeypatch.setattr(
        "content.bio.project_locate.locate_project_files",
        lambda pat, limit=6, ctx=None: {"matches": [
            {"rel": "m.h5", "tier": "live sandbox", "site": "siteB",
             "opens": "fetches from siteB on open"}]})
    _, err = _resolve_project_path("m.h5", {"thread_id": "t"}, must_exist=True,
                                   enforce_sandbox=False)
    assert err and "not local" in err and "siteB" in err
