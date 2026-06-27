"""Phase 3, 3.3a — entity-type registry capability flags."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_phase3_33_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"), "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "a"), "ABA_WORK_DIR": str(Path(_tmp) / "w"),
    "DATA_DIR": str(Path(_tmp) / "d"),
})
sys.path.insert(0, str(ROOT / "backend"))
import content.bio  # noqa: E402,F401 — loads the entity-type registry
from core.entity_types import registry as R   # noqa: E402


def test_capability_flags():
    assert R.types_with("is_artifact") == {"figure", "table", "cell"}
    assert R.types_with("is_run") == {"analysis"}
    assert R.artifact_groups() == {"plots": "figure", "tables": "table"}
    assert R.by_title_storage("figure") == "artifact_file"
    assert R.by_title_storage("cell") == "artifact_file"
    assert R.by_title_storage("analysis") == "run_dir"
    assert R.by_title_storage("dataset") == "data_path"
    assert R.by_title_storage("claim") is None


def test_sidebar_count_capability():
    assert R.types_with("sidebar", "count") == {"result", "claim", "finding"}


def test_sidebar_renders_registry_counts():
    from core.graph._schema import init_db
    init_db()
    from core.graph.entities import create_entity
    from core.graph.derivation import manual
    from content.bio.cards.sidebar import render_bio_project_sidebar
    create_entity(entity_type="result", title="r1", derivation=manual())
    out = render_bio_project_sidebar()
    assert "results=1" in out
