"""Phase 2, 2B.3 — the promote spine carries derived_from lineage + actor at
creation (no backfill lag)."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_phase2_2b3_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"),
    "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "artifacts"),
    "ABA_WORK_DIR": str(Path(_tmp) / "work"),
    "DATA_DIR": str(Path(_tmp) / "data"),
})
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db   # noqa: E402
init_db()
import content.bio  # noqa: E402,F401 — registry + edge rules
from core.graph.entities import create_entity, get_entity   # noqa: E402
from core.graph.actor import acting_as   # noqa: E402
from content.bio.lifecycle.promote import (   # noqa: E402
    promote_figure_to_result, promote_results_to_finding,
)


def test_promote_figure_to_result_lineage_and_actor():
    fig = create_entity(entity_type="figure", title="f", artifact_path="/tmp/f.png", exec_id="e1")
    with acting_as("human:local"):
        rid = promote_figure_to_result(fig, "it works")
    r = get_entity(rid)
    assert r["derivation"] == {"kind": "derived_from", "sources": [fig]}
    assert r["actor"] == "human:local"


def test_promote_results_to_finding_lineage():
    f1 = create_entity(entity_type="figure", title="f1", artifact_path="/tmp/f1.png", exec_id="e1")
    f2 = create_entity(entity_type="figure", title="f2", artifact_path="/tmp/f2.png", exec_id="e2")
    with acting_as("human:local"):
        r1 = promote_figure_to_result(f1, "res1")
        r2 = promote_figure_to_result(f2, "res2")
        fid = promote_results_to_finding([r1, r2], "combined")
    f = get_entity(fid)
    assert f["derivation"] == {"kind": "derived_from", "sources": [r1, r2]}
    assert f["actor"] == "human:local"


def test_promotion_record_from_promoted_result():
    from core.graph.provenance import promotion_record
    fig = create_entity(entity_type="figure", title="f", artifact_path="/tmp/f.png", exec_id="e1")
    with acting_as("human:local"):
        rid = promote_figure_to_result(fig, "interp")
    rec = promotion_record(get_entity(rid))
    assert rec["by"] == "human:local"        # who
    assert rec["from"] == [fig]              # from
    assert rec["at"]                         # when (timestamp)


def test_promotion_record_none_for_non_derived():
    from core.graph.provenance import promotion_record
    assert promotion_record({"derivation": {"kind": "manual"}}) is None
    assert promotion_record({"derivation": {"kind": "exec", "exec_id": "x"}}) is None
    assert promotion_record(None) is None
