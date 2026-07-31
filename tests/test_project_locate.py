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


def test_terminal_kernels_are_not_walked_as_live_sandboxes(monkeypatch, tmp_path):
    """list_kernels returns every kernel the workspace KNOWS — on an aged
    deployment that's mostly terminal rows (179 of 184 live-measured). T1 is
    the LIVE tier: walking every terminal kernel's surviving jobdir grew the
    search unboundedly with deployment age and labeled long-stopped scratch
    'live sandbox'. Floor AND ceiling: the running kernel is walked, the
    stopped/died ones are not (their bytes stay reachable via the manifest
    tier and the run resolvers, which deliberately DO reach them)."""
    for kid in ("krn_live", "krn_old", "krn_dead"):
        jd = tmp_path / "ws" / "site-local" / kid
        jd.mkdir(parents=True)
        (jd / f"{kid}_file.csv").write_text("x")

    class _C:
        def sync_call(self, name, *a):
            return {"kernels": [
                {"kernel_id": "krn_live", "site": "local",
                 "jobdir": "krn_live", "state": "running"},
                {"kernel_id": "krn_old", "site": "local",
                 "jobdir": "krn_old", "state": "stopped"},
                {"kernel_id": "krn_dead", "site": "local",
                 "jobdir": "krn_dead", "state": "died"},
            ]}
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _C())
    monkeypatch.setattr("core.compute.adapter.weft_workspace",
                        lambda: tmp_path / "ws")
    monkeypatch.setattr(pl, "_manifest_tier", lambda *a, **k: None)
    out = pl.locate_project_files("*_file.csv")
    names = {h["name"] for h in out["matches"]}
    assert names == {"krn_live_file.csv"}, names
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
        # Enumerators of their OWN known directories — package/registry
        # discovery and display listings, not name→file resolution:
        "backend/content/bio/services.py",             # tool-module discovery (*.py in its code dir)
        "backend/content/bio/tools/discovery.py",      # dist-info enumeration inside an env prefix
        "backend/content/bio/tools/__init__.py",       # tool-registry package walk
        "backend/content/bio/advisors/__init__.py",    # advisor yaml discovery in its own package dir
        "backend/content/bio/lifecycle/revisions.py",  # bundle-dir file listing for display
    }
    offenders = []
    for f in (ROOT / "backend/content/bio").rglob("*.py"):
        rel = str(f.relative_to(ROOT)).replace(os.sep, "/")
        if "mcp_servers" in rel and "tools/file_io" in rel:
            continue                        # registration shim only
        src = f.read_text()
        # Every idiom that can privately answer "where is this file" — the
        # original os.walk/rglob pair let a newest-wins `.glob()` resolver
        # slip straight past the invariant.
        if re.search(r"\bos\.walk\(|\brglob\(|\.glob\(|\biterdir\(\)"
                     r"|\bos\.scandir\(|\bos\.listdir\(|\bglob\.glob\(",
                     src) and rel not in allowed:
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


def test_truncation_is_confessed_not_silent(monkeypatch):
    """PROVEN: every tier capped the shared hits list AT the limit, so the
    total could never exceed it and `truncated` was structurally False — the
    exact silent-truncation class the module docstring disclaims."""
    _no_kernels(monkeypatch)
    monkeypatch.setattr(pl, "_manifest_tier", lambda *a, **k: None)
    from core.config import project_data_dir
    from core.projects import current_project_id
    d = Path(str(project_data_dir(str(current_project_id() or "default"))))
    d.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (d / f"trunc_probe_{i}.csv").write_text("x")
    out = pl.locate_project_files("trunc_probe_*.csv", limit=3)
    assert len(out["matches"]) == 3
    assert out["truncated"] is True, \
        "5 matches, limit 3, truncated False — silent truncation"


def test_collision_order_is_recency_not_string_length(monkeypatch, tmp_path):
    """PROVEN: 'newest first' sorted by LEN(iso-string) — i.e. by whether the
    timestamp happened to carry microseconds — not by time. An older file with
    a fractional mtime outranked a file 100s newer."""
    _no_kernels(monkeypatch)
    monkeypatch.setattr(pl, "_manifest_tier", lambda *a, **k: None)
    from core.config import project_data_dir
    from core.projects import current_project_id
    d = Path(str(project_data_dir(str(current_project_id() or "default"))))
    d.mkdir(parents=True, exist_ok=True)
    older = d / "order_a.csv"; newer = d / "order_b.csv"
    older.write_text("x"); newer.write_text("y")
    os.utime(older, (1_700_000_000.5, 1_700_000_000.5))   # fractional → longer iso
    os.utime(newer, (1_700_000_100.0, 1_700_000_100.0))   # whole-second → shorter iso
    out = pl.locate_project_files("order_*.csv")
    assert [h["name"] for h in out["matches"]] == ["order_b.csv", "order_a.csv"], \
        [(h["name"], h["mtime"]) for h in out["matches"]]


def test_relpath_pattern_matches_local_tiers(monkeypatch):
    """PROVEN tier-parity: remote and manifest tiers match rel paths, the local
    walks matched basenames only — same glob, different tiers, different
    answers. 'sub/x.csv' must find the file in a walked local tier too."""
    _no_kernels(monkeypatch)
    monkeypatch.setattr(pl, "_manifest_tier", lambda *a, **k: None)
    from core.config import project_data_dir
    from core.projects import current_project_id
    d = Path(str(project_data_dir(str(current_project_id() or "default"))))
    (d / "subq").mkdir(parents=True, exist_ok=True)
    (d / "subq" / "rp_probe.csv").write_text("x")
    out = pl.locate_project_files("subq/rp_probe.csv")
    assert out["matches"], "rel-path pattern found nothing in a walked tier"


def test_exec_wrapper_never_lands_in_produced(tmp_path):
    """PROVEN: the runner's own wrapper script (mtime == since_ts — it IS the
    reference stamp) leaked into produced[] as a skipped-shape row on every
    stateless run, so every run advertised the harness's machinery as a user
    output and find_files matched it across all recent runs."""
    from core.exec.run import harvest_artifacts
    scratch = tmp_path / "s"
    scratch.mkdir()
    wrapper = scratch / "script.py"
    wrapper.write_text("print('x')")
    since = wrapper.stat().st_mtime
    (scratch / "model_state.ckpt").write_bytes(b"\1" * 64)
    _, _, files, _ = harvest_artifacts(scratch, since_ts=since, project_id="prjWr")
    names = [x.get("original_name") for x in files]
    assert "script.py" not in names, f"wrapper leaked into produced[]: {names}"
    assert "model_state.ckpt" in names, "real skipped-shape output vanished"


def test_sandboxed_write_resolver_refuses_outside_door_hits(monkeypatch, tmp_path):
    """Containment invariant: the door fallback is a READ convenience — it must
    never hand a WRITE resolver (enforce_sandbox=True, i.e. edit_file) a path
    outside the project's editable roots. A unique same-name hit in a served
    store copy (content-addressed bytes) or a live kernel jobdir (possibly
    another project's) would otherwise be edited IN PLACE."""
    from content.bio.tools.file_io import _resolve_project_path
    outside = tmp_path / "elsewhere" / "m.csv"
    outside.parent.mkdir(parents=True)
    outside.write_text("x")
    monkeypatch.setattr(
        "content.bio.project_locate.locate_project_files",
        lambda pat, limit=6, ctx=None: {"matches": [
            {"path": str(outside), "tier": "run output"}]})
    got, err = _resolve_project_path("m.csv", {"thread_id": "t"},
                                     must_exist=True, enforce_sandbox=True)
    assert got == "" and err, "door hit outside the sandbox resolved for a write"
    assert "sandbox" in err, err
    assert "run output" in err, "the hit's fate/tier is not named"


def test_sandboxed_write_resolver_accepts_contained_hit(monkeypatch):
    """The other side: a door hit INSIDE the editable roots still resolves for
    writes — otherwise the guard gets disabled the first time it cries wolf."""
    from content.bio.tools.file_io import _resolve_project_path
    from core.config import project_work_dir
    from core import projects
    pid = projects.current() or "default"
    inside = Path(str(project_work_dir(pid))) / "sub" / "w_inside.csv"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("x")
    monkeypatch.setattr(
        "content.bio.project_locate.locate_project_files",
        lambda pat, limit=6, ctx=None: {"matches": [
            {"path": str(inside), "tier": "work scratch"}]})
    got, err = _resolve_project_path("w_inside.csv", {"thread_id": "t"},
                                     must_exist=True, enforce_sandbox=True)
    assert err is None and got == str(inside), (got, err)


def test_sandboxed_write_mixed_hits_resolves_the_contained_one(monkeypatch, tmp_path):
    """WIDE: one hit inside + one outside must resolve to the inside one — the
    outside hit was never a legal write target, so it must not manufacture an
    ambiguity error (nor, worse, win the resolution)."""
    from content.bio.tools.file_io import _resolve_project_path
    from core.config import project_work_dir
    from core import projects
    pid = projects.current() or "default"
    inside = Path(str(project_work_dir(pid))) / "mix.csv"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("x")
    outside = tmp_path / "mix.csv"
    outside.write_text("y")
    monkeypatch.setattr(
        "content.bio.project_locate.locate_project_files",
        lambda pat, limit=6, ctx=None: {"matches": [
            {"path": str(outside), "tier": "run output"},
            {"path": str(inside), "tier": "work scratch"}]})
    got, err = _resolve_project_path("mix.csv", {"thread_id": "t"},
                                     must_exist=True, enforce_sandbox=True)
    assert err is None and got == str(inside), (got, err)


def test_read_resolver_still_reaches_outside_hits(monkeypatch, tmp_path):
    """Degenerate guard on the fix itself: with enforce_sandbox=False (reads),
    outside hits must STILL resolve — the containment filter is write-scoped."""
    from content.bio.tools.file_io import _resolve_project_path
    outside = tmp_path / "r.csv"
    outside.write_text("x")
    monkeypatch.setattr(
        "content.bio.project_locate.locate_project_files",
        lambda pat, limit=6, ctx=None: {"matches": [
            {"path": str(outside), "tier": "run output"}]})
    got, err = _resolve_project_path("r.csv", {"thread_id": "t"},
                                     must_exist=True, enforce_sandbox=False)
    assert err is None and got == str(outside)


def test_prompt_surfaces_teach_name_first_on_all_tiers():
    """Documentation defaults are behavior: the paths guidance must teach the
    name-first contract on EVERY prompt tier (full/standard AND the slim file
    lean tiers swap in — the tier-coverage gap class), and the old
    root-anchoring idiom must be gone."""
    for f in ("backend/system_bundle/rules/behavior.md",
              "backend/system_bundle/rules/behavior_slim.md"):
        text = (ROOT / f).read_text()
        assert "refer to it by NAME" in text, f"{f}: name-first teaching absent"
        assert "find_files(" in text, f"{f}: lookup tool not named"
        assert "never guess from `DATA_DIR/<name>`" not in text.lower(), \
            f"{f}: old root-anchoring idiom survives"


def test_oversize_messages_name_the_working_handle(tmp_path):
    """'Too large to copy' is half a message — the other half is the handle
    that still works. Producer-fed: harvest an over-cap file and assert the
    warning names the file and the lookup route."""
    from core.exec.run import harvest_artifacts, _MAX_HARVEST_BYTES
    scratch = tmp_path / "s"
    scratch.mkdir()
    big = scratch / "huge_out.parquet"
    big.write_bytes(b"\0" * (_MAX_HARVEST_BYTES + 1))
    _, _, files, warnings = harvest_artifacts(scratch, since_ts=0,
                                              project_id="prjW")
    w = " ".join(warnings)
    assert "huge_out.parquet" in w and "find_files" in w, warnings
    assert "YOURS BY NAME" in w, warnings


def test_skipped_shape_file_keeps_its_name_real(tmp_path):
    """A new file whose suffix is outside every harvest keep-list must still
    land in produced[] as a link-only row — previously it vanished with no
    copy, no row, no warning, and its name stopped being real."""
    from core.exec.run import harvest_artifacts
    scratch = tmp_path / "s"
    scratch.mkdir()
    (scratch / "model_state.ckpt").write_bytes(b"\1" * 128)
    _, _, files, _ = harvest_artifacts(scratch, since_ts=0, project_id="prjS")
    row = next((x for x in files
                if x.get("original_name") == "model_state.ckpt"), None)
    assert row is not None, f"skipped-shape file vanished: {files}"
    assert row.get("link_only") and row.get("skipped_shape")
