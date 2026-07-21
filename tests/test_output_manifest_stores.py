"""Run-output manifest — the user-facing surface a session opens FIRST.

Guards the three defects a live-session audit surfaced (2026-07-21), all in
how a Run's outputs are advertised:
  1. artifact-index collision — directory-store members sharing one artifact_id
     (basename-keyed lookup collapsed same-leaf members), so pin/dedup/address
     couldn't tell distinct outputs apart;
  2. store-member explosion — a chunked store surfaced as hundreds of internal
     shard rows instead of ONE logical store output;
  3. a plain table/file next to captured figures must appear in the manifest.

All synthetic + domain-free: a scratch dir with a figure, a table, and a
generic `.zarr` store; the manifest is built through the real code path.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

_RT = tempfile.mkdtemp(prefix="aba_manifest_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "r.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402  (app + type registry)
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity, get_entity  # noqa: E402
from content.bio.lifecycle import runs as runs_mod  # noqa: E402

init_db()


def _mk_run_with_outputs(tmp: Path) -> str:
    """A Run whose artifact_path holds a figure, a table, and a `.zarr` store —
    the disk-scan path (jupyter / by-reference runs) that rglob-walks outputs."""
    base = tmp / "runwork"
    (base).mkdir(parents=True, exist_ok=True)
    (base / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    (base / "results.csv").write_text("a,b\n1,2\n3,4\n")
    store = base / "processed.zarr"
    for member in ("zarr.json", "axes/zarr.json", "axes/x/zarr.json",
                   "fields/zarr.json", "fields/a/c/0"):
        p = store / member
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    rid = create_entity(entity_type="analysis", title="R",
                        artifact_path=str(base))
    return rid if isinstance(rid, str) else rid["id"]


def _outputs(rid) -> list[dict]:
    runs_mod.refresh_output_manifest(rid)
    md = get_entity(rid).get("metadata") or {}
    # the manifest patches the nested `run.outputs` path
    return (md.get("run") or {}).get("outputs") or md.get("outputs") or []


def test_directory_store_collapses_to_one_output():
    with tempfile.TemporaryDirectory() as td:
        rid = _mk_run_with_outputs(Path(td))
        outs = _outputs(rid)
        stores = [o for o in outs if o.get("kind") == "store"]
        assert len(stores) == 1, [o["label"] for o in outs]
        st = stores[0]
        assert st["label"] == "processed.zarr"
        assert st["n_members"] == 5           # all shards folded in
        # NO raw shard rows leak into the manifest
        assert not [o for o in outs if o["label"].startswith("processed.zarr/")]


def test_table_and_figure_both_present():
    with tempfile.TemporaryDirectory() as td:
        rid = _mk_run_with_outputs(Path(td))
        outs = _outputs(rid)
        kinds = {o["label"]: o["kind"] for o in outs}
        assert kinds.get("results.csv") == "table"   # the manifest-gap class
        assert kinds.get("plot.png") == "figure"


def test_no_artifact_id_collision_across_outputs():
    """Every output that HAS an artifact_id has a DISTINCT one — the collision
    (many rows sharing a store's id) cannot recur once members are folded."""
    with tempfile.TemporaryDirectory() as td:
        rid = _mk_run_with_outputs(Path(td))
        outs = _outputs(rid)
        ids = [o["artifact_id"] for o in outs if o.get("artifact_id")]
        assert len(ids) == len(set(ids)), ids


def test_store_root_detection():
    f = runs_mod._store_root_of
    assert list(f("out/x.zarr/axes/0")) == ["out/x.zarr"]
    assert list(f("out/x.zarr")) == ["out/x.zarr"]
    assert list(f("fig.png")) == []
    assert list(f("a/b.zarr/c.zarr/d")) == ["a/b.zarr"]   # outermost wins
