"""P2b: close_run's retention hook (_retain_run_outputs).

Isolated unit test — monkeypatches artifacts_for_run + retention.retain so it
verifies the WIRING (per-target retain of the Run's produced paths, labeled,
layout=label) without standing up the entity DB / weft. Grounded in
misc/output_durability.md §6.3 (deferred pin on the live session kernel).

Run: python tests/test_close_run_retention.py   (pytest-optional; see __main__)
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_ret_hook_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from content.bio.lifecycle import runs as runsmod  # noqa: E402


def test_retain_run_outputs_pins_produced_per_target(monkeypatch):
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    # figures + tables + an oversize link-only file; a blank name is filtered
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png"},
        {"original_name": "samples/A/qc.csv"},
        {"original_name": "big.h5ad"},     # the >50 MB link-only crown jewel (§A0)
        {"original_name": ""},             # dropped
    ])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])   # nothing retained yet
    calls = []
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.append((target, kw))
                        or {"state": "pinned-pending"})

    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a", "jb_b"]})

    assert {c[0] for c in calls} == {"krn_a", "jb_b"}       # one retain per target
    for target, kw in calls:
        assert kw["label"] == "run-1"
        assert kw["layout"] == "label"
        assert kw["background"] is True
        # this Run's own produced paths, sorted, blank removed (incl. the large file)
        assert kw["include"] == ["big.h5ad", "samples/A/qc.csv", "umap.png"]


def test_retain_run_outputs_noop_without_targets(monkeypatch):
    import core.compute.retention as retmod
    called = []
    monkeypatch.setattr(retmod, "retain", lambda *a, **k: called.append(1))
    runsmod._retain_run_outputs("run-1", {})               # jupyter / no kernel
    assert called == []


def test_retain_run_outputs_noop_when_nothing_produced(monkeypatch):
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    called = []
    monkeypatch.setattr(retmod, "retain", lambda *a, **k: called.append(1))
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a"]})
    assert called == []


def test_retain_run_outputs_swallows_retain_errors(monkeypatch):
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    monkeypatch.setattr(artmod, "artifacts_for_run",
                        lambda rid: [{"original_name": "x.png"}])

    monkeypatch.setattr(retmod, "retained", lambda **kw: [])

    def _boom(*a, **k):
        raise RuntimeError("weft unreachable")
    monkeypatch.setattr(retmod, "retain", _boom)
    # must not raise — Run close cannot be blocked by retention
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a"]})


def test_retain_includes_declared_unsurfaced_outputs(monkeypatch):
    """B2/§6 rank-1: a recipe-declared output the agent didn't surface is retained anyway;
    one already surfaced (by basename) isn't re-added as a spurious literal."""
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    monkeypatch.setattr(runsmod, "get_entity", lambda pid: {
        "id": pid, "metadata": {"steps": [
            {"expected_outputs": ["model.pt", "figs/umap.png", "DE results"]}]}}
        if pid == "plan-1" else None)
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "samples/A/umap.png"},   # surfaced (basename umap.png)
        {"original_name": "qc.csv"},
    ])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    calls = []
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.append(kw) or {"state": "pinned-pending"})
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a"],
                                          "plan_entity_id": "plan-1"})
    inc = calls[0]["include"]
    assert "model.pt" in inc                       # declared + unsurfaced → added
    assert "samples/A/umap.png" in inc             # produced surfaced path kept
    assert "umap.png" not in inc                   # NOT re-added (basename already produced)
    assert "DE results" not in inc                 # bare description (no '.') skipped


def test_declared_output_names_helper(monkeypatch):
    monkeypatch.setattr(runsmod, "get_entity", lambda pid: {
        "metadata": {"steps": [{"expected_outputs": ["a.csv", "sub/b.rds"]},
                               {"expected_outputs": ["notes"]}]}})
    assert runsmod._declared_output_names({"plan_entity_id": "p"}) == {"a.csv", "b.rds"}
    assert runsmod._declared_output_names({}) == set()   # no plan → empty


def test_retain_attributes_files_per_target_when_known(monkeypatch):
    """Multi-target Run (a restart minted a 2nd kernel_id): a file whose producing
    target is known (via its exec record's weft_target) is retained ONLY against that
    target — not blanket to every target, which would settle the others as a spurious
    pin_missing for a file they never produced (review finding #4)."""
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    from core.graph import exec_records
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "a.png", "exec_id": "e1"},
        {"original_name": "b.png", "exec_id": "e2"},
    ])
    monkeypatch.setattr(exec_records, "get", lambda eid: {
        "e1": {"weft_target": "krn_a"}, "e2": {"weft_target": "krn_b"}}.get(eid))
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])   # nothing already retained
    calls: dict = {}
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.__setitem__(target, kw["include"])
                        or {"state": "pinned-pending"})
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a", "krn_b"]})
    assert calls == {"krn_a": ["a.png"], "krn_b": ["b.png"]}   # each its OWN file only


def test_retain_multi_target_unattributable_falls_back_to_all(monkeypatch):
    """When a file's producer can't be determined (no exec_id / unrecorded target),
    retain it against ALL targets — a redundant pin_missing beats losing the file."""
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    from core.graph import exec_records
    monkeypatch.setattr(artmod, "artifacts_for_run",
                        lambda rid: [{"original_name": "orphan.png"}])   # no exec_id
    monkeypatch.setattr(exec_records, "get", lambda eid: None)
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    calls: dict = {}
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.__setitem__(target, kw["include"])
                        or {"state": "pinned-pending"})
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a", "krn_b"]})
    assert calls == {"krn_a": ["orphan.png"], "krn_b": ["orphan.png"]}   # safe: both


def test_retain_is_cumulative_and_skips_transient(monkeypatch):
    """P1: obvious scratch (tmp/, cache/, *.tmp, chunk_*) excluded, and the submitted
    selection is CUMULATIVE — a path already covered by a pending retain row is
    re-submitted alongside the new file (weft's put_retained is an INSERT-OR-REPLACE per
    target, so a delta submit would drop earlier turns' pins at settlement). A call with
    nothing newly decided issues no retain at all (idempotent skip)."""
    import json
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png"},               # already pending (below)
        {"original_name": "big.h5ad"},               # NEW keeper
        {"original_name": "tmp/scratch.dat"},        # transient DIR
        {"original_name": "cache/x.bin"},            # transient DIR
        {"original_name": "run.tmp"},                # transient glob
        {"original_name": "chunk_003.parquet"},      # transient glob
    ])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"state": "pinned-pending", "selection": json.dumps({"include": ["umap.png"]})}])
    calls = []
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.append(kw) or {"state": "pinned-pending"})
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a"]})
    # cumulative: earlier pin re-submitted with the new keeper; transients dropped
    assert calls[0]["include"] == ["big.h5ad", "umap.png"]

    # nothing newly decided → NO retain call (the stored selection already covers it)
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"state": "pinned-pending",
         "selection": json.dumps({"include": ["big.h5ad", "umap.png"]})}])
    calls.clear()
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a"]})
    assert calls == []


def test_retain_includes_directory_stores_from_jobdir(monkeypatch):
    """P1 / #71: a directory-shaped store in the Run's jobdir (invisible to the file-only
    harvest) enters the keeper set as a directory literal; stores under transient dirs
    don't, and the store's chunk contents are not enumerated."""
    import tempfile as _tf
    from pathlib import Path as _P
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    jd = _P(_tf.mkdtemp(prefix="aba_jobdir_"))
    (jd / "dataset_cube.zarr" / "c").mkdir(parents=True)
    (jd / "dataset_cube.zarr" / "c" / "0.0").write_bytes(b"\0" * 8)
    (jd / "tmp" / "scratch.zarr").mkdir(parents=True)          # transient dir → skipped
    monkeypatch.setattr(runsmod, "_run_jobdirs", lambda rid: [str(jd)])
    monkeypatch.setattr(artmod, "artifacts_for_run",
                        lambda rid: [{"original_name": "umap.png"}])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    calls = []
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.append(kw) or {"state": "pinned-pending"})
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a"]})
    assert calls[0]["include"] == ["dataset_cube.zarr", "umap.png"]


def test_is_transient_matrix():
    T = runsmod._is_transient
    assert T("tmp/a.csv") and T("d/__pycache__/x.pyc") and T("x.tmp") and T("chunk_0.bin")
    assert not T("umap.png") and not T("samples/A/qc.csv") and not T("big.h5ad")


_TESTS = [
    test_retain_run_outputs_pins_produced_per_target,
    test_retain_run_outputs_noop_without_targets,
    test_retain_run_outputs_noop_when_nothing_produced,
    test_retain_run_outputs_swallows_retain_errors,
    test_retain_includes_declared_unsurfaced_outputs,
    test_declared_output_names_helper,
    test_retain_attributes_files_per_target_when_known,
    test_retain_multi_target_unattributable_falls_back_to_all,
    test_retain_is_cumulative_and_skips_transient,
    test_retain_includes_directory_stores_from_jobdir,
    test_is_transient_matrix,
]


def _standalone() -> int:
    import inspect
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v, raising=True):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in _TESTS:
        mp = _MP()
        try:
            t(mp) if "monkeypatch" in inspect.signature(t).parameters else t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


def test_attribution_falls_back_to_compute_job_id(monkeypatch):
    """A BACKGROUND job's exec record has no `weft_target` — the same identity
    lives at compute.job_id. Attribution must use it: without the fallback a
    bg-job keeper went to ALL targets and kernel sandboxes got data.missing
    retains (live ui_remote_run_badges finding, 2026-07-19)."""
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    import core.graph.exec_records as ermod
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "big.bin", "exec_id": "ex_job"},
        {"original_name": "peek.txt", "exec_id": "ex_krn"},
    ])
    monkeypatch.setattr(ermod, "get", lambda eid: {
        "ex_job": {"compute": {"job_id": "jb_b"}},          # bg job shape
        "ex_krn": {"weft_target": "krn_a"},                 # kernel shape
    }.get(eid))
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    calls = []
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.append((target, kw)))
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a", "jb_b"]})
    got = {t: kw["include"] for t, kw in calls}
    assert got.get("jb_b") == ["big.bin"]
    assert got.get("krn_a") == ["peek.txt"]


def test_data_missing_on_covered_rel_is_not_an_alert(monkeypatch):
    """Unknown-producer fallback sends a rel to every target; targets that
    never held it refuse with data.missing. If ANOTHER target accepted the
    rel, that refusal is noise, not an alert — the file IS being kept."""
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    import core.graph.exec_records as ermod
    from core.compute.errors import ComputeError
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "big.bin", "exec_id": "ex_unknown"},
    ])
    monkeypatch.setattr(ermod, "get", lambda eid: {})       # producer unknown
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])

    def _retain(target, **kw):
        if target == "krn_a":
            raise ComputeError("data.missing",
                               "selection matched no files", stage="staging")

    monkeypatch.setattr(retmod, "retain", _retain)
    alerts = []
    monkeypatch.setattr(runsmod, "_note_retention_alert",
                        lambda rid, md, msg: alerts.append(msg))
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a", "jb_b"]})
    assert alerts == [None]      # jb_b accepted big.bin → no alert

    # …but when NO target holds it, the alert must still fire
    def _retain_all_missing(target, **kw):
        raise ComputeError("data.missing",
                           "selection matched no files", stage="staging")
    monkeypatch.setattr(retmod, "retain", _retain_all_missing)
    alerts.clear()
    runsmod._retain_run_outputs("run-1", {"weft_targets": ["krn_a", "jb_b"]})
    assert alerts and alerts[0] and "data.missing" in alerts[0]


if __name__ == "__main__":
    raise SystemExit(_standalone())
