"""Gap 1 (bucket): harvest_artifacts must bucket into the project passed by the
background/Slurm runner, NOT the ambient current_project_id() — which is the
no-project `_workspace` fallback on a compute node that never bound the project.
Bucketing to _workspace is what orphaned the Seurat job's figures from the Run.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_bkt_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec.run import harvest_artifacts  # noqa: E402


def test_harvest_buckets_by_passed_project_id():
    scratch = Path(tempfile.mkdtemp())
    (scratch / "results.csv").write_text("gene,logfc\nA,1.2\n")
    plots, tables, files, warns = harvest_artifacts(scratch, project_id="prj_unit_x")
    assert len(tables) == 1
    assert tables[0]["url"].startswith("/artifacts/prj_unit_x/"), tables[0]["url"]
    # the copy physically landed in that project's artifact store
    from core.config import project_artifacts_dir
    copied = list(project_artifacts_dir("prj_unit_x").glob("*.csv"))
    assert len(copied) == 1


def test_harvest_falls_back_to_ambient_when_no_project_id():
    scratch = Path(tempfile.mkdtemp())
    (scratch / "out.csv").write_text("x\n1\n")
    plots, tables, files, warns = harvest_artifacts(scratch)   # no project_id
    # falls back to the ambient project (whatever is bound — `_workspace` when
    # nothing is, or the test harness's project under conftest). The point is it
    # uses current_project_id(), not a hard-coded id.
    from core.config import current_project_id
    assert tables and tables[0]["url"].startswith(f"/artifacts/{current_project_id()}/"), tables[0]["url"]


def test_harvest_since_ts_skips_older_files():
    scratch = Path(tempfile.mkdtemp())
    old = scratch / "old.csv"; old.write_text("a\n1\n")
    import os as _os, time as _t
    base = old.stat().st_mtime + 10            # pretend the run started later
    _os.utime(old, (base - 20, base - 20))      # old.csv is well before the cutoff
    new = scratch / "new.csv"; new.write_text("b\n2\n")
    _os.utime(new, (base + 5, base + 5))        # new.csv is after the cutoff
    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=base, project_id="prj_since")
    names = {t["original_name"] for t in tables}
    assert "new.csv" in names and "old.csv" not in names, names
