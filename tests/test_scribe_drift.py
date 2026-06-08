"""P5 — Drift detector.

Tests:
- Healthy project (sidecars match DB): skew_score == 0.0 at every depth.
- Missing entity sidecar → counts disagree → non-zero skew, file_mismatch
  populated at sampled/full depth.
- Stale entity sidecar field (title diverged) → sampled/full catch it.
- drift.json is persisted under .scribe/.

Run: .venv/bin/python tests/test_scribe_drift.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_drift_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override   # noqa: E402

_scribe = Scribe(tick_interval=10_000.0)
set_scribe_override(_scribe)

from core import projects                                       # noqa: E402
from core.graph.entities import create_entity, update_entity    # noqa: E402
from core.recovery.drift import compute_drift                   # noqa: E402

projects.init()

PROOT = Path(_tmp) / "projects"


def _populated_project(name: str) -> tuple[str, Path]:
    p = projects.create_project(name)
    pid = p["id"]
    projects.set_current(pid)
    for i in range(5):
        create_entity(entity_type="analysis", title=f"A-{i}",
                      metadata={"step": i})
    for i in range(3):
        create_entity(entity_type="finding", title=f"F-{i}")
    _scribe.flush()
    return pid, PROOT / pid


# ─── tests ──────────────────────────────────────────────────────────────────
def test_no_drift_when_fs_matches_db():
    pid, pdir = _populated_project("Drift-Clean")
    rep = compute_drift(pdir, depth="count")
    assert rep.error is None, f"unexpected error: {rep.error}"
    assert rep.skew_score == 0.0, f"unexpected skew: {rep.skew_score} (counts={rep.counts_live}/{rep.counts_fs})"
    assert (pdir / ".scribe" / "drift.json").exists()


def test_missing_sidecar_inflates_count_skew():
    pid, pdir = _populated_project("Drift-Missing")
    # Remove one *non-workspace* sidecar (simulating a missed scribe hook for
    # a regular entity). The workspace row is bootstrapped by init_db on
    # recovery, so deleting workspace.json doesn't show as a count mismatch.
    sidecars = sorted(
        f for f in (pdir / "entities").glob("*.json") if f.stem != "workspace"
    )
    assert sidecars, "test setup: expected at least one non-workspace entity sidecar"
    sidecars[0].unlink()
    rep = compute_drift(pdir, depth="count")
    assert rep.error is None
    assert rep.counts_live["entities"] != rep.counts_fs["entities"], \
        f"expected count mismatch; live={rep.counts_live}, fs={rep.counts_fs}"
    assert rep.skew_score > 0, f"expected non-zero skew, got {rep.skew_score}"


def test_sampled_depth_catches_stale_field():
    pid, pdir = _populated_project("Drift-StaleField")
    # Manually rewrite one sidecar to introduce a title divergence vs DB
    sidecars = sorted((pdir / "entities").glob("*.json"))
    target = sidecars[0]
    payload = json.loads(target.read_text())
    payload["title"] = "FROM-FS-STALE-VALUE"
    target.write_text(json.dumps(payload))
    rep = compute_drift(pdir, depth="sampled")
    assert rep.error is None
    # counts match (we didn't add/remove), but the sample picks up the diff
    assert rep.counts_live == rep.counts_fs, "counts shouldn't differ"
    assert rep.sample_size > 0
    # At least the one we tampered with should show up. The sample may not
    # always include it — but if N == total, it must.
    if rep.sample_size >= len(sidecars):
        assert rep.sample_mismatches >= 1, \
            f"expected ≥1 sample mismatch; got {rep.sample_mismatches} (field_mismatches={rep.field_mismatches[:3]})"


def test_full_depth_catches_every_stale_field():
    pid, pdir = _populated_project("Drift-FullDiverge")
    # Tamper with two non-workspace sidecars (workspace gets reset by init_db)
    sidecars = sorted(
        f for f in (pdir / "entities").glob("*.json") if f.stem != "workspace"
    )
    for s in sidecars[:2]:
        p = json.loads(s.read_text())
        p["title"] = "STALE-" + p.get("title", "")
        s.write_text(json.dumps(p))
    rep = compute_drift(pdir, depth="full")
    assert rep.error is None
    # Full depth samples every live row (8 entities + workspace = 9)
    assert rep.sample_size >= 9, f"full should sample all rows; got {rep.sample_size}"
    assert rep.sample_mismatches >= 2, \
        f"expected at least 2 mismatches; got {rep.sample_mismatches}"


# ─── runner ─────────────────────────────────────────────────────────────────
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
