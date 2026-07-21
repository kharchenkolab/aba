"""The regtest transport/mechanism-truth oracle (regtest/harness/transport.py)
must flag executions that self-identify as legacy (compute.substrate != the
substrate) and pass substrate-stamped ones — reading the real mechanism-truth
surface (GET /api/runs/{rid}/execs) over the real app.

This is the oracle class the substrate migration lacked: outcome parity
between lanes made every outcome test blind to WHICH lane ran; the compute
stamp knew all along, and this reads it.

Run: python tests/test_transport_oracle.py   (also pytest-collectable)
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_transport_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "t.db"))
_REPO = Path(__file__).resolve().parents[1]
for p in (str(_REPO / "backend"), str(_REPO / "regtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import main  # noqa: E402
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
from core.graph import exec_records  # noqa: E402
from harness.transport import transport_truth, transport_verdict  # noqa: E402

init_db()


def _client():
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def _mk_run(title: str) -> str:
    out = create_entity(entity_type="analysis", title=title,
                        metadata={"thread_id": "t", "run_state": "open"})
    return out if isinstance(out, str) else out["id"]


def _mk_exec(rid: str, compute: dict | None, tag: str) -> str:
    return exec_records.create(
        thread_id="t", run_id=rid, tool_name="run_python", status="ok",
        code="1+1", started_at="2026-07-20T00:00:00+00:00",
        cwd=tempfile.mkdtemp(prefix=f"aba_x_{tag}_", dir=_RT),
        payload={"kind": "script", "language": "python",
                 **({"compute": compute} if compute is not None else {})})


def test_substrate_stamped_execs_pass(tmp_path):
    # fixtures are created INSIDE the client context: app startup (re)binds
    # the active project DB, and under the full suite an earlier module may
    # have left a different binding — pre-startup fixtures would 404 on the
    # route (the `checked` non-vacuity assert below is what catches that)
    with _client() as c:
        rid = _mk_run("Substrate Run")
        _mk_exec(rid, {"substrate": "weft", "site": "local",
                       "kernel_id": "krn_ok"}, "w")
        out = transport_truth(c, "default", run_ids=[rid])
    assert out["failures"] == [], out
    assert out["checked"] == 1                 # non-vacuous: it really looked


def test_legacy_stamped_exec_is_flagged(tmp_path):
    """A record from the legacy local lane stamps substrate='local' — the
    oracle must flag it (this is the exact record the pre-cutover default
    deployment writes for every interactive block)."""
    with _client() as c:
        rid = _mk_run("Legacy Run")
        _mk_exec(rid, {"substrate": "local", "site": "local"}, "l")
        _mk_exec(rid, {"substrate": "weft", "site": "local",
                       "kernel_id": "krn_ok2"}, "w2")
        out = transport_truth(c, "default", run_ids=[rid])
    assert len(out["failures"]) == 1, out
    assert out["failures"][0].startswith("transport:legacy_exec:")
    assert "substrate='local'" in out["failures"][0]
    assert out["checked"] == 2


def test_absent_compute_block_not_adjudicated(tmp_path):
    """v1 predicate: records with no compute block (older records,
    doctrine-exempt direct-exec lanes) are not flagged — the post-cutover
    invariant tightens absence separately."""
    with _client() as c:
        rid = _mk_run("Bare Run")
        _mk_exec(rid, None, "b")
        out = transport_truth(c, "default", run_ids=[rid])
    assert out["failures"] == [] and out["checked"] == 0


def test_verdict_non_vacuity_proven_flag():
    """A clean check with checked>0 is a genuine PASS (proven); a clean check
    with checked==0 verified NOTHING — proven False, but still PASS by default
    so it can't perturb an accepted baseline's mech_pass."""
    proven = transport_verdict({"failures": [], "checked": 3})
    assert proven == {"verdict": "PASS", "fails": [], "checked": 3, "proven": True}
    vacuous = transport_verdict({"failures": [], "checked": 0})
    assert vacuous["verdict"] == "PASS" and vacuous["proven"] is False


def test_verdict_strict_fails_the_vacuous_pass():
    """Opt-in strict mode gives the non-vacuity check teeth: checked==0 FAILs
    with a typed reason, while a genuine failure still FAILs (unchanged)."""
    v = transport_verdict({"failures": [], "checked": 0}, strict=True)
    assert v["verdict"] == "FAIL" and v["proven"] is False
    assert v["fails"] and "unproven" in v["fails"][0]
    # a real legacy finding fails regardless of strictness, and is not masked
    real = transport_verdict({"failures": ["transport:legacy_exec:r/x"],
                              "checked": 2}, strict=False)
    assert real["verdict"] == "FAIL" and real["fails"] == ["transport:legacy_exec:r/x"]
    # strict never downgrades a proven clean pass
    assert transport_verdict({"failures": [], "checked": 5},
                             strict=True)["verdict"] == "PASS"


_TESTS = [test_substrate_stamped_execs_pass,
          test_legacy_stamped_exec_is_flagged,
          test_absent_compute_block_not_adjudicated,
          test_verdict_non_vacuity_proven_flag,
          test_verdict_strict_fails_the_vacuous_pass]


def _standalone() -> int:
    import inspect
    import traceback
    rc = 0
    for t in _TESTS:
        try:
            kw = {}
            if "tmp_path" in inspect.signature(t).parameters:
                kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="aba_t_", dir=_RT))
            t(**kw)
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
