"""patch_metadata — the atomic single-key metadata write (lost-update fix).

The prevalent get_entity → mutate dict → update_entity(metadata=whole_blob)
pattern REPLACES the blob, so two concurrent writers to DIFFERENT keys drop
each other's write (poll-loop weft_targets append vs threadpool manifest/
cancel writers — either could aim retention at nothing). These tests pin the
primitive's contract: named keys only, whole-key replace (no deep merge),
None removes, everything else untouched.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_pmd_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()
from core.graph.entities import (  # noqa: E402
    create_entity, get_entity, patch_metadata,
)


def test_patch_sets_only_named_keys():
    eid = create_entity(entity_type="analysis", title="patch run",
                        metadata={"a": 1})
    patch_metadata(eid, {"b": {"x": 2}})
    md = get_entity(eid)["metadata"]
    assert md == {"a": 1, "b": {"x": 2}}


def test_interleaved_writers_do_not_drop_each_other():
    """The exact lost-update scenario: W1 snapshots the entity, W2 patches
    its own key, then W1 patches ITS key from the stale snapshot's era —
    with whole-blob writes W2's key vanished; with patches both survive."""
    eid = create_entity(entity_type="analysis", title="race run", metadata={})
    _stale_snapshot = get_entity(eid)                    # W1 reads
    patch_metadata(eid, {"weft_targets": ["wj_9"]})      # W2 (poll loop) writes
    patch_metadata(eid, {"run": {"outputs": [1, 2]}})    # W1 writes its key
    md = get_entity(eid)["metadata"]
    assert md["weft_targets"] == ["wj_9"]
    assert md["run"]["outputs"] == [1, 2]


def test_patch_replaces_key_wholly_and_none_removes():
    eid = create_entity(entity_type="analysis", title="replace run",
                        metadata={"run": {"old": 1, "keep": 2}, "z": 3})
    patch_metadata(eid, {"run": {"new": 9}})
    md = get_entity(eid)["metadata"]
    assert md["run"] == {"new": 9}          # replaced, NOT deep-merged
    assert md["z"] == 3
    patch_metadata(eid, {"z": None})
    assert "z" not in get_entity(eid)["metadata"]


def test_patch_missing_entity_returns_none():
    assert patch_metadata("ana_nope", {"k": 1}) is None


def test_note_run_site_records_remote_placement():
    """The Run card's verdict reads metadata.run.sites — the legacy
    executor:'remote-hpc' marker has no writer since the sbatch lane retired,
    so remote runs claimed 'ran locally' (browser-study finding). local is
    the default story and is never recorded; sites dedupe."""
    from content.bio.lifecycle.runs import note_run_site
    eid = create_entity(entity_type="analysis", title="placement run",
                        metadata={"run": {"outputs": []}})
    note_run_site(eid, "local")          # default story — not recorded
    assert "sites" not in (get_entity(eid)["metadata"].get("run") or {})
    note_run_site(eid, "hpc")
    note_run_site(eid, "hpc")            # dedup
    note_run_site(eid, "mendel")
    run_meta = get_entity(eid)["metadata"]["run"]
    assert run_meta["sites"] == ["hpc", "mendel"]
    assert run_meta["outputs"] == []     # sibling keys untouched


if __name__ == "__main__":
    rc = 0
    for t in (test_patch_sets_only_named_keys,
              test_interleaved_writers_do_not_drop_each_other,
              test_patch_replaces_key_wholly_and_none_removes,
              test_patch_missing_entity_returns_none,
              test_note_run_site_records_remote_placement):
        try:
            t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
    raise SystemExit(rc)


def test_nested_paths_share_a_parent_without_clobbering():
    """Dotted keys land at nested paths: manifest (run.outputs), cancel
    (run.status), placement (run.sites) all live under `run` — each writer
    touches ONLY its field, so the same-key RMW race (recheck-confirmed:
    a manifest refresh could revert a cancellation) is structurally gone."""
    eid = create_entity(entity_type="analysis", title="nested run",
                        metadata={"run": {"status": "running", "outputs": [1]}})
    patch_metadata(eid, {"run.status": "cancelled", "run.finished_at": "t1"})
    md = get_entity(eid)["metadata"]
    assert md["run"]["status"] == "cancelled"
    assert md["run"]["outputs"] == [1]          # sibling untouched
    assert md["run"]["finished_at"] == "t1"
    # parent auto-created when absent
    patch_metadata(eid, {"deep.new.leaf": 7})
    assert get_entity(eid)["metadata"]["deep"]["new"]["leaf"] == 7
    # None removes at the nested path only
    patch_metadata(eid, {"run.finished_at": None})
    md = get_entity(eid)["metadata"]
    assert "finished_at" not in md["run"] and md["run"]["status"] == "cancelled"


def test_append_metadata_list_is_atomic_append():
    from core.graph.entities import append_metadata_list
    eid = create_entity(entity_type="analysis", title="append run",
                        metadata={})
    append_metadata_list(eid, "run.sites", "siteA")
    append_metadata_list(eid, "run.sites", "siteB")
    assert get_entity(eid)["metadata"]["run"]["sites"] == ["siteA", "siteB"]
    append_metadata_list(eid, "weft_targets", "t1")
    assert get_entity(eid)["metadata"]["weft_targets"] == ["t1"]


def test_truly_concurrent_writers_lose_nothing():
    """GENUINELY concurrent (the prior 'interleaved' test was sequential —
    recheck finding): two threads hammer the SAME top-level object, one
    appending to run.sites, one rewriting run.outputs. Every append must
    survive and the last outputs write must be intact — with any
    read-modify-write in the chain, appends vanish under this load."""
    import threading
    from core.graph.entities import append_metadata_list
    eid = create_entity(entity_type="analysis", title="conc run",
                        metadata={"run": {"status": "running"}})
    N = 40
    errs: list = []

    def _appender():
        try:
            for i in range(N):
                append_metadata_list(eid, "run.sites", f"s{i}")
        except Exception as e:  # noqa: BLE001
            errs.append(f"append: {e}")

    def _setter():
        try:
            for i in range(N):
                patch_metadata(eid, {"run.outputs": [{"n": i}]})
        except Exception as e:  # noqa: BLE001
            errs.append(f"set: {e}")

    ts = [threading.Thread(target=_appender), threading.Thread(target=_setter)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert not errs, errs
    md = get_entity(eid)["metadata"]
    assert md["run"]["status"] == "running"                 # untouched field intact
    assert [s for s in md["run"]["sites"]] == [f"s{i}" for i in range(N)]
    assert md["run"]["outputs"] == [{"n": N - 1}]
